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

from .config import ConfigError, load_config
from .coordinator import reconcile, tick
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
    service = actions.add_parser(
        "launch-agent", help="Print a per-user LaunchAgent property list"
    )
    service.add_argument("--session", type=UUID, required=True)
    service.add_argument("--live", action="store_true")
    probe = actions.add_parser(
        "test-handoff", help="Run the disposable transcript test"
    )
    probe.add_argument("--session", type=UUID, required=True)
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
    if version not in {"0.96.0", "0.100.0"} or (args.live and version != "0.96.0"):
        raise RuntimeError(
            f"Conductor {version} has not been validated for this run mode"
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


def main(argv=None):
    args = parser().parse_args(argv)
    try:
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
            return test_handoff(load_config(args.config), str(args.session))
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
