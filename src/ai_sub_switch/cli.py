import argparse
import fcntl
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from uuid import UUID

from .config import ConfigError, init_config, load_config
from .coordinator import UnlockCycle, reconcile, tick
from .desktop import Desktop, Handoff
from .providers import Codex, Cswap
from .recovery import Conductor, Queue

HOME = Path.home()
ROOT = HOME / "Library/Application Support/ai-sub-switch"
DEFAULT_CONFIG = HOME / ".config/ai-sub-switch/config.toml"
APP = Path("/Applications/Conductor.app/Contents/Info.plist")
DEFAULT_CODEX = HOME / "Library/Application Support/com.conductor.app/bin/codex"


def parser():
    root = argparse.ArgumentParser(prog="aisub")
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    root.add_argument("--state", type=Path, default=ROOT / "state.sqlite3")
    root.add_argument("--cswap", default=shutil.which("cswap"))
    root.add_argument("--codex", default=str(DEFAULT_CODEX))
    actions = root.add_subparsers(dest="command", required=True)
    actions.add_parser("init", help="Create the example configuration")
    for command in ("status", "pause", "resume", "doctor", "validate"):
        actions.add_parser(command)
    run = actions.add_parser("run", help="Watch one explicitly selected Conductor chat")
    run.add_argument("--session", type=UUID, required=True)
    run.add_argument("--live", action="store_true")
    run.add_argument(
        "--include-current",
        action="store_true",
        help="Include last blocked turn when enrolling this session",
    )
    run.add_argument("--once", action="store_true")
    run.add_argument(
        "--unlock-test",
        action="store_true",
        help="Wait for a lock and unlock before retrying one disposable Claude chat",
    )
    service = actions.add_parser(
        "launch-agent", help="Print a per-user LaunchAgent property list"
    )
    service.add_argument("--session", type=UUID, required=True)
    service.add_argument("--live", action="store_true")
    probe = actions.add_parser(
        "test-handoff", help="Run the disposable transcript test"
    )
    probe.add_argument("--session", type=UUID, required=True)
    probe.add_argument(
        "--same-thread",
        action="store_true",
        help="Retry a quota-blocked disposable Claude chat in place",
    )
    return root


def launch_agent(args):
    executable = str(Path(sys.argv[0]).absolute())
    command = [
        executable,
        "--config",
        str(args.config.resolve()),
        "--state",
        str(args.state.resolve()),
        "--cswap",
        args.cswap,
        "--codex",
        args.codex,
        "run",
        "--session",
        str(args.session),
    ]
    if args.live:
        command.append("--live")
    print(
        plistlib.dumps(
            {
                "Label": "com.ai-sub-switch.agent",
                "ProgramArguments": command,
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 30,
                "LimitLoadToSessionType": "Aqua",
                "ProcessType": "Background",
            }
        ).decode(),
        end="",
    )


def detect_version():
    with APP.open("rb") as stream:
        return plistlib.load(stream)["CFBundleShortVersionString"]


def run(args, config, queue, desktop):
    session = str(args.session)
    if args.live:
        ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lease = ROOT / "live.lock" if args.live else args.state.with_suffix(".lock")
    fd = os.open(lease, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another recovery runner owns this state", file=sys.stderr)
            return 4
        return watch(args, config, queue, desktop, session)
    finally:
        os.close(fd)


def watch(args, config, queue, desktop, session):
    version = detect_version()
    if version not in {"0.96.0", "0.100.0"}:
        raise RuntimeError(
            f"Conductor {version} has not been validated for this runner"
        )
    baseline = queue.get(f"baseline:{session}")
    if baseline is None:
        since = datetime.now(UTC)
        if args.include_current:
            from datetime import timedelta

            since -= timedelta(hours=8)
        queue.set(f"baseline:{session}", since.isoformat())
    else:
        since = datetime.fromisoformat(baseline)
    observer = Conductor(config.conductor_db)
    provider = Cswap(args.cswap)
    codex = Codex(args.codex)
    handoff = Handoff(desktop)
    if args.unlock_test:
        return watch_unlock_test(
            args, config, queue, desktop, session, since, observer, provider, handoff
        )
    last = None
    while True:
        try:
            report = reconcile(queue, observer, session) or tick(
                queue,
                config,
                session,
                since,
                observer,
                provider,
                desktop,
                handoff,
                args.live and queue.get("paused") != "1",
                codex,
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            sqlite3.Error,
            subprocess.SubprocessError,
        ) as exc:
            report = f"Waiting: {exc}"
        if report != last or args.once:
            print(report, flush=True)
            last = report
        if args.once:
            return 0
        sleep(10 if not desktop.unlocked() else 30)


def watch_unlock_test(
    args, config, queue, desktop, session, since, observer, provider, handoff
):
    from dataclasses import replace
    from time import monotonic

    if not args.live or not args.include_current or args.once:
        raise RuntimeError("--unlock-test requires --live and --include-current")
    observer.disposable_workspace(session)
    desktop.require()
    claude = tuple(a for a in config.accounts if a.provider == "claude")
    scoped = replace(
        config,
        accounts=claude,
        order=tuple(key for key in config.order if key in {a.id for a in claude}),
        mappings=(),
    )
    active = provider.active(claude)
    turn = observer.blocked(session, since, active)
    if not turn or not active:
        raise RuntimeError(
            "The disposable chat must have a current Claude quota failure"
        )
    if queue.get("paused") == "1":
        raise RuntimeError("Recovery is paused")
    if any(
        row["session"] == session
        and row["turn"] == turn.turn_id
        and row["state"] != "pending"
        for row in queue.status()
    ):
        raise RuntimeError("This disposable turn already has a recovery attempt")
    queue.set("active", active)
    queue.add(turn)
    print(
        "Blocked disposable turn queued. Bring Conductor forward, lock the Mac for at least 5 seconds, then unlock.",
        flush=True,
    )
    gate = UnlockCycle()
    end = monotonic() + 180
    last = None
    while monotonic() < end:
        state = desktop.session_state()
        if not gate.observe(state):
            report = "Mac locked. Recovery is queued." if state == "locked" else None
        else:
            try:
                desktop.front()
            except RuntimeError:
                if last != "Unlocked. Bring Conductor forward to continue.":
                    print("Unlocked. Bring Conductor forward to continue.", flush=True)
                    last = "Unlocked. Bring Conductor forward to continue."
                sleep(1)
                continue
            if observer.busy():
                if last != "Waiting for other Claude work to settle":
                    print("Waiting for other Claude work to settle", flush=True)
                    last = "Waiting for other Claude work to settle"
                sleep(2)
                continue
            if provider.active(claude) != active:
                raise RuntimeError("Claude login changed during the test")
            selection = provider.usage(claude)
            original = selection.get(active)
            if original is None or original.state != "exhausted":
                raise RuntimeError(
                    "The active Claude account is no longer confirmed exhausted"
                )
            from .rules import select_target

            target = select_target(turn, scoped, selection, datetime.now(UTC))
            if target.kind != "handoff" or target.account_id == active:
                raise RuntimeError(
                    "The test needs a different Claude account with available quota"
                )
            if queue.get("paused") == "1":
                raise RuntimeError("Recovery was paused")
            report = tick(
                queue,
                scoped,
                session,
                since,
                observer,
                provider,
                desktop,
                handoff,
                True,
            )
            if any(
                row["session"] == session
                and row["turn"] == turn.turn_id
                and row["state"] in {"verified", "retrying", "attention", "cancelled"}
                for row in queue.status()
            ):
                print(report, flush=True)
                return 0 if report == "Same-thread Retry recorded by Conductor" else 3
        if report and report != last:
            print(report, flush=True)
            last = report
        sleep(1)
    raise RuntimeError("Unlock test timed out; check the queue before restarting")


def test_handoff(config, session):
    from time import monotonic
    from uuid import uuid4

    from .domain import BlockedTurn, ModelChoice

    observer = Conductor(config.conductor_db)
    workspace = observer.disposable_workspace(session)
    source = ModelChoice("claude", "sonnet-5-5-1m", "high")
    turn = BlockedTurn(
        workspace, session, "", "", None, source, datetime.now(UTC), None
    )
    title = observer.title(turn)
    if title != f"Disposable shared login test {session[:8]}":
        raise RuntimeError("The disposable source chat could not be verified")
    desktop = Desktop()
    desktop.require()
    handoff = Handoff(desktop)
    marker = uuid4().hex
    destination = handoff.prepare(
        turn, ModelChoice("codex", "gpt-6.2-sol", "high"), title, marker
    )
    handoff.submit(turn, destination, marker, title)
    until = monotonic() + 12
    while monotonic() < until:
        if observer.receipt(destination, marker, session):
            print(
                f"Conductor recorded the test transcript and continuation in {destination}"
            )
            return 0
        sleep(0.25)
    print(
        "Test submission could not be confirmed. Inspect the new chat before retrying.",
        file=sys.stderr,
    )
    return 3


def test_retry(config, session, state):
    observer = Conductor(config.conductor_db)
    observer.disposable_workspace(session)
    desktop = Desktop()
    handoff = Handoff(desktop)
    with Queue(state) as queue:
        existing = reconcile(queue, observer, session)
        if existing:
            print(existing)
            return 0 if existing.startswith("Same-thread Retry recorded") else 3
        since = datetime.min.replace(tzinfo=UTC)
        turn = observer.blocked(session, since, None)
        if turn is None:
            raise RuntimeError(
                "The disposable chat has no current Claude quota failure"
            )
        handoff.prepare_retry(turn)
        cursor = observer.retry_cursor(session)
        current = observer.blocked(session, since, None)
        if current is None or current.turn_id != turn.turn_id:
            raise RuntimeError("The disposable chat changed before Retry")
        queue.add(turn)
        if not queue.transition(turn, "pending", "retrying", str(cursor)):
            raise RuntimeError("This disposable turn already has a recovery attempt")
        handoff.submit_retry(turn)
        result = reconcile(queue, observer, session, wait=True)
        print(result)
        return 0 if result == "Same-thread Retry recorded by Conductor" else 3


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            init_config(args.config)
            print(f"Created configuration: {args.config}")
            return 0
        if args.command == "validate":
            load_config(args.config)
            print("Configuration is valid")
            return 0
        if args.command in {"status", "pause", "resume"}:
            with Queue(args.state) as queue:
                if args.command == "status":
                    print(
                        json.dumps(
                            {
                                "paused": queue.get("paused") == "1",
                                "recoveries": queue.status(),
                            },
                            indent=2,
                        )
                    )
                else:
                    queue.set("paused", "1" if args.command == "pause" else "0")
                    print(
                        "Recovery paused"
                        if args.command == "pause"
                        else "Recovery enabled"
                    )
            return 0
        if args.command == "doctor":
            desktop = Desktop()
            print(
                f"Conductor {detect_version()}; desktop unlocked: {desktop.unlocked()}; "
                f"Accessibility trusted: {bool(desktop.ax.AXIsProcessTrusted())}; cswap found: {bool(args.cswap)}"
            )
            return 0
        if args.command == "test-handoff":
            config = load_config(args.config)
            if args.same_thread:
                return test_retry(config, str(args.session), args.state)
            return test_handoff(config, str(args.session))
        if not args.cswap:
            raise RuntimeError("Pass --cswap with the path to the enrolled switcher")
        config = load_config(args.config)
        if args.command == "launch-agent":
            launch_agent(args)
            return 0
        with Queue(args.state) as queue:
            return run(args, config, queue, Desktop())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    except (ImportError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(exc, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
