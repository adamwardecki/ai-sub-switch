import json
import subprocess
from datetime import UTC, datetime

from .domain import Availability


def _timestamp(value):
    if isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
            return result if result.tzinfo else None
        except ValueError:
            return None
    return None


class Cswap:
    def __init__(self, executable):
        self.executable = executable

    def _command(self, *args):
        result = subprocess.run(
            [self.executable, *args, "--json"],
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("cswap did not return JSON") from None
        if result.returncode or data.get("schemaVersion") != 1 or data.get("error"):
            raise RuntimeError("cswap command failed; check login and Keychain access")
        return data

    @staticmethod
    def matches(row, account):
        return account.backend_ref in (
            row.get("alias"),
            row.get("email"),
            str(row.get("number")),
        )

    def active(self, accounts):
        row = self._command("status").get("active") or {}
        matches = [account.id for account in accounts if self.matches(row, account)]
        return matches[0] if len(matches) == 1 else None

    def usage(self, accounts):
        rows = self._command("list").get("accounts", [])
        now = datetime.now(UTC)
        result = {}
        for account in accounts:
            matched = [row for row in rows if self.matches(row, account)]
            if len(matched) != 1:
                continue
            row = matched[0]
            status = row.get("usageStatus")
            usage = row.get("usage") or {}
            windows = [
                usage[k]
                for k in ("fiveHour", "sevenDay")
                if isinstance(usage.get(k), dict)
            ]
            age = row.get("usageAgeSeconds")
            state, reset = "unknown", None
            if status == "relogin_required":
                state = "reauth"
            elif (
                status == "ok"
                and windows
                and isinstance(age, (float, int))
                and 0 <= age <= 60
            ) and all(
                isinstance(window.get("pct"), (int, float)) for window in windows
            ):
                full = [window for window in windows if window["pct"] >= 100]
                state = "exhausted" if full else "ready"
                resets = [_timestamp(window.get("resetsAt")) for window in full]
                reset = max((value for value in resets if value), default=None)
            if row.get("disabled") or row.get("kind") == "api_key":
                state = "unknown"
            result[account.id] = Availability(
                account.id, state, reset, now, status or ""
            )
        return result

    def activate(self, account, accounts):
        self._command("switch", account.backend_ref)
        if self.active(accounts) != account.id:
            raise RuntimeError("Claude account identity could not be verified")


class Codex:
    def __init__(self, executable):
        self.executable = executable

    def usage(self, account):
        import selectors
        from time import monotonic

        state, reset = "unknown", None
        with (
            subprocess.Popen(
                [self.executable, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            ) as process,
            selectors.DefaultSelector() as selector,
        ):
            selector.register(process.stdout, selectors.EVENT_READ)
            buffer = b""
            deadline = monotonic() + 12

            def request(method, id_=None, params=None):
                nonlocal buffer
                message = {"method": method}
                if id_ is not None:
                    message["id"] = id_
                if params is not None:
                    message["params"] = params
                process.stdin.write((json.dumps(message) + "\n").encode())
                if id_ is None:
                    return {}
                while monotonic() < deadline:
                    if b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        reply = json.loads(line)
                        if reply.get("id") == id_:
                            return reply.get("result") or {}
                        continue
                    if not selector.select(max(0, deadline - monotonic())):
                        break
                    block = process.stdout.read(65536)
                    if not block or len(buffer) > 1_000_000:
                        break
                    buffer += block
                return {}

            try:
                init = request(
                    "initialize",
                    1,
                    {"clientInfo": {"name": "aisub", "version": "0.1.0"}},
                )
                if init:
                    request("initialized")
                    identity = (
                        request("account/read", 2, {"refreshToken": False}).get(
                            "account"
                        )
                        or {}
                    )
                    if identity.get("type") == "chatgpt":
                        reply = request("account/rateLimits/read", 3)
                        limits = reply.get("rateLimits") or {}
                        windows = [
                            limits[key]
                            for key in ("primary", "secondary")
                            if limits.get(key)
                        ]
                        if windows and all(
                            isinstance(w.get("usedPercent"), (int, float))
                            for w in windows
                        ):
                            full = [w for w in windows if w["usedPercent"] >= 100]
                            allowed = reply.get("ordinaryUsageAllowed")
                            state = "exhausted" if full or allowed is False else "ready"
                            times = [
                                w["resetsAt"]
                                for w in full
                                if type(w.get("resetsAt")) is int
                                and 0 < w["resetsAt"] < 4_102_444_800
                            ]
                            reset = (
                                datetime.fromtimestamp(max(times), UTC)
                                if times
                                else None
                            )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
        return Availability(account.id, state, reset, datetime.now(UTC), "")
