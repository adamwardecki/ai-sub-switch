import re
import subprocess
from time import monotonic, sleep
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


class DesktopUnavailable(RuntimeError):
    pass


class Desktop:
    def __init__(self):
        import AppKit
        import ApplicationServices
        import Quartz

        self.appkit = AppKit
        self.ax = ApplicationServices
        self.cg = Quartz

    def unlocked(self):
        state = self.cg.CGSessionCopyCurrentDictionary() or {}
        return bool(
            state.get("kCGSSessionOnConsoleKey")
            and state.get("kCGSessionLoginDoneKey")
            and not state.get("CGSSessionScreenIsLocked")
        )

    def require(self):
        if not self.unlocked():
            raise DesktopUnavailable("Waiting for an unlocked desktop")
        if not self.ax.AXIsProcessTrusted():
            raise DesktopUnavailable(
                "Accessibility permission is required for this runner"
            )

    def front(self):
        self.require()
        app = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.bundleIdentifier() != "com.conductor.app":
            raise DesktopUnavailable("Waiting for Conductor to be the foreground app")
        return app.processIdentifier()

    def attribute(self, node, name):
        error, value = self.ax.AXUIElementCopyAttributeValue(node, name, None)
        return value if error == 0 else None

    def snapshot(self):
        pid = self.front()
        root = self.ax.AXUIElementCreateApplication(pid)
        self.ax.AXUIElementSetMessagingTimeout(root, 0.7)
        window = self.attribute(root, "AXFocusedWindow")
        nodes, stack = [], [window] if window else []
        while stack and len(nodes) < 1500:
            node = stack.pop()
            role = self.attribute(node, "AXRole")
            title = (
                self.attribute(node, "AXTitle")
                or self.attribute(node, "AXDescription")
                or ""
            )
            nodes.append((node, role, title))
            stack.extend(reversed(self.attribute(node, "AXChildren") or []))
        routes = []
        for node, role, _ in nodes:
            if role == "AXWebArea" and (url := self.attribute(node, "AXURL")):
                routes.append(urlsplit(str(url)))
        return pid, nodes, routes

    def press(self, pid, node):
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed while preparing recovery")
        error = self.ax.AXUIElementPerformAction(node, "AXPress")
        if error:
            raise RuntimeError(f"Conductor control rejected AXPress ({error})")

    def key(self, pid, code, flags=0, value=None):
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed while preparing recovery")
        for down in (True, False):
            event = self.cg.CGEventCreateKeyboardEvent(None, code, down)
            if value:
                self.cg.CGEventKeyboardSetUnicodeString(event, len(value), value)
            self.cg.CGEventSetFlags(event, flags)
            self.cg.CGEventPostToPid(pid, event)


def _route(routes, workspace):
    for route in routes:
        if route.path.rstrip("/").endswith(f"/workspace/{workspace}"):
            return parse_qs(route.query).get("sessionId", [None])[0]
    return None


def _unique(nodes, role, label):
    found = [node for node, kind, title in nodes if kind == role and label(title)]
    return found[0] if len(found) == 1 else None


class Handoff:
    LABELS: ClassVar[dict[str, str]] = {
        "sonnet-5-5-1m": "Sonnet 5.5",
        "opus-5-5-1m": "Opus 5.5",
        "gpt-6.2-sol": "GPT-6.2 Sol",
        "sonnet-5-1m": "Sonnet 5",
    }

    def __init__(self, desktop):
        self.desktop = desktop

    def _wait(self, workspace, condition):
        until = monotonic() + 5
        while monotonic() < until:
            pid, nodes, routes = self.desktop.snapshot()
            session = _route(routes, workspace)
            if session and condition(session, nodes):
                return pid, session, nodes
            sleep(0.2)
        raise RuntimeError("Conductor did not expose the expected chat or control")

    def prepare(self, turn, model, title, marker):
        label = self.LABELS.get(model.model)
        if not label:
            raise RuntimeError("Unsupported model label for this Conductor version")
        UUID(turn.workspace_id)
        UUID(turn.session_id)
        self.desktop.front()
        subprocess.run(
            [
                "/usr/bin/open",
                f"conductor://workspace?id={turn.workspace_id}&session={turn.session_id}",
            ],
            timeout=5,
            check=True,
        )
        pid, _, nodes = self._wait(
            turn.workspace_id, lambda sid, _: sid == turn.session_id
        )
        command = self.desktop.cg.kCGEventFlagMaskCommand
        if model.provider == turn.source.provider:
            self.desktop.key(pid, 17, command)
        else:
            picker = _unique(
                nodes, "AXPopUpButton", lambda n: n.startswith("Change agent")
            )
            if picker is None:
                raise RuntimeError("Model picker could not be located")
            self.desktop.press(pid, picker)
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid == turn.session_id
                    and any(
                        kind == "AXTextField" and "Search models" in name
                        for _, kind, name in ns
                    )
                ),
            )
            self.desktop.key(pid, 0, value=label)
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid == turn.session_id
                    and any(name == f"{label} Codex" for _, _, name in ns)
                ),
            )
            choice = [node for node, _, name in nodes if name == f"{label} Codex"]
            if len(choice) != 1:
                raise RuntimeError("Destination model is ambiguous")
            self.desktop.press(pid, choice[0])

        pid, destination, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid != turn.session_id
                and any(name == "Add chat transcripts:" for _, _, name in ns)
            ),
        )
        return self._draft(turn, model, title, label, marker, pid, destination, nodes)

    def _draft(self, turn, model, title, label, marker, pid, destination, nodes):
        def selected(nodes):
            pattern = (
                rf"^Change agent \({re.escape(label)} · {re.escape(model.effort.title())}\)$"
                if model.effort
                else rf"^Change agent \({re.escape(label)}(?: · [^)]*)?\)$"
            )
            return _unique(
                nodes, "AXPopUpButton", lambda name: bool(re.match(pattern, name))
            )

        if selected(nodes) is None:
            picker = _unique(
                nodes, "AXPopUpButton", lambda n: n.startswith("Change agent")
            )
            if picker is None:
                raise RuntimeError("Destination model picker is unavailable")
            self.desktop.press(pid, picker)
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid == destination
                    and any(name == "Search models" for _, _, name in ns)
                ),
            )
            self.desktop.key(pid, 0, value=label)
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid == destination
                    and any(name == f"{label} Claude Code" for _, _, name in ns)
                ),
            )
            choice = [node for node, _, name in nodes if name == f"{label} Claude Code"]
            if len(choice) != 1:
                raise RuntimeError("Destination model is ambiguous")
            self.desktop.press(pid, choice[0])
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: sid == destination and selected(ns) is not None,
            )

        source = _unique(nodes, "AXButton", lambda name: name == title)
        if source is None:
            raise RuntimeError("Source is missing from the transcript picker")
        self.desktop.press(pid, source)
        attachment = f"Remove Transcript of {title}.md"
        pid, _, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid == destination and any(name == attachment for _, _, name in ns)
            ),
        )
        composer = _unique(
            nodes, "AXTextArea", lambda n: n == "What do you want to work on?"
        )
        if composer is None:
            raise RuntimeError("Destination composer is missing")
        value = self.desktop.attribute(composer, "AXValue") or ""
        expected = f"Transcript of {title}.md"
        if str(value).strip() != expected:
            raise RuntimeError("Destination contains an unexpected draft")
        if self.desktop.ax.AXUIElementSetAttributeValue(composer, "AXFocused", True):
            raise RuntimeError("Could not focus destination composer")
        prompt = (
            " Read the attached transcript and continue the unfinished task. "
            "Preserve its constraints and check the current workspace and completed actions "
            f"before repeating any work. Recovery reference: {marker}."
        )
        self.desktop.key(pid, 0, value=prompt)
        self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid == destination
                and any(
                    kind == "AXTextArea"
                    and marker in str(self.desktop.attribute(node, "AXValue"))
                    for node, kind, _ in ns
                )
                and any(name == attachment for _, _, name in ns)
            ),
        )
        return destination

    def submit(self, turn, destination, marker):
        pid, _, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid == destination
                and any(
                    kind == "AXTextArea"
                    and marker in str(self.desktop.attribute(node, "AXValue"))
                    for node, kind, _ in ns
                )
            ),
        )
        send = _unique(nodes, "AXButton", lambda name: name.startswith("Send message"))
        if send is None:
            raise RuntimeError("Cannot identify the destination Send button")
        self.desktop.press(pid, send)
