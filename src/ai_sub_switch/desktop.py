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

    def session_state(self):
        state = self.cg.CGSessionCopyCurrentDictionary() or {}
        if not state.get("kCGSSessionOnConsoleKey") or not state.get(
            "kCGSessionLoginDoneKey"
        ):
            return "unknown"
        return "locked" if state.get("CGSSessionScreenIsLocked") else "unlocked"

    def unlocked(self):
        return self.session_state() == "unlocked"

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
            if not title and role == "AXStaticText":
                title = self.attribute(node, "AXValue") or ""
            if not title and role in {"AXTextField", "AXTextArea"}:
                title = (
                    self.attribute(node, "AXPlaceholderValue")
                    or self.attribute(node, "AXHelp")
                    or ""
                )
            nodes.append((node, role, str(title)))
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

    def click_control(self, pid, node):
        # A menu result can acknowledge AXPress without activating its Chromium row.
        # Use the bounds of the unique matched result and check the app before clicking.
        ax = self.ax
        position = self.attribute(node, "AXPosition")
        size = self.attribute(node, "AXSize")
        if position is None or size is None:
            raise RuntimeError(
                "Conductor did not expose bounds for the matched control"
            )
        ok_position, point = ax.AXValueGetValue(position, ax.kAXValueCGPointType, None)
        ok_size, extent = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
        if not ok_position or not ok_size or extent.width <= 0 or extent.height <= 0:
            raise RuntimeError(
                "Conductor returned invalid bounds for the matched control"
            )
        center = self.cg.CGPointMake(
            point.x + extent.width / 2, point.y + extent.height / 2
        )
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed before clicking the control")
        for kind in (self.cg.kCGEventLeftMouseDown, self.cg.kCGEventLeftMouseUp):
            event = self.cg.CGEventCreateMouseEvent(
                None, kind, center, self.cg.kCGMouseButtonLeft
            )
            self.cg.CGEventPost(self.cg.kCGHIDEventTap, event)

    def key(self, pid, code, flags=0, value=None):
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed while preparing recovery")
        for down in (True, False):
            event = self.cg.CGEventCreateKeyboardEvent(None, code, down)
            if value:
                self.cg.CGEventKeyboardSetUnicodeString(event, len(value), value)
            self.cg.CGEventSetFlags(event, flags)
            self.cg.CGEventPostToPid(pid, event)

    def paste(self, pid, text, verify, field=None):
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed before paste")
        if field is not None and not self.cg.CGPreflightPostEventAccess():
            raise DesktopUnavailable(
                "macOS has not granted keyboard event access to this runner"
            )
        board = self.appkit.NSPasteboard.generalPasteboard()
        saved = []
        for item in board.pasteboardItems() or []:
            copy = self.appkit.NSPasteboardItem.alloc().init()
            for kind in item.types():
                data = item.dataForType_(kind)
                if data is not None:
                    copy.setData_forType_(data, kind)
            if copy.types():
                saved.append(copy)
        board.clearContents()
        if not board.setString_forType_(text, self.appkit.NSPasteboardTypeString):
            if saved:
                board.writeObjects_(saved)
            raise RuntimeError("Could not stage the text for paste")
        change = board.changeCount()
        try:
            if field is None:
                self.key(pid, 9, self.cg.kCGEventFlagMaskCommand)
            else:
                if pid != self.front() or not self.focused(pid, field):
                    raise DesktopUnavailable("Conductor focus changed before paste")
                for down in (True, False):
                    event = self.cg.CGEventCreateKeyboardEvent(None, 9, down)
                    self.cg.CGEventSetFlags(event, self.cg.kCGEventFlagMaskCommand)
                    self.cg.CGEventPost(self.cg.kCGHIDEventTap, event)
            until = monotonic() + 2
            while monotonic() < until:
                if verify():
                    return True
                sleep(0.05)
            return False
        finally:
            if board.changeCount() == change:
                board.clearContents()
                if saved:
                    board.writeObjects_(saved)

    def focused(self, pid, field):
        from CoreFoundation import CFEqual

        if self.attribute(field, "AXFocused") is True:
            return True
        root = self.ax.AXUIElementCreateApplication(pid)
        focused = self.attribute(root, "AXFocusedUIElement")
        return focused is not None and bool(CFEqual(focused, field))

    def wait_focus(self, pid, field):
        until = monotonic() + 0.75
        while monotonic() < until:
            if self.focused(pid, field):
                return True
            sleep(0.05)
        return False

    def type_search(self, pid, field, value):
        if pid != self.front():
            raise DesktopUnavailable("Conductor changed before model search")
        ax = self.ax
        if not self.focused(pid, field):
            ax.AXUIElementSetAttributeValue(field, "AXFocused", True)
        if not self.wait_focus(pid, field):
            self.press(pid, field)
            if not self.wait_focus(pid, field):
                raise RuntimeError("Conductor model search did not take keyboard focus")

        def matches():
            observed = self.attribute(field, "AXValue")
            return observed is not None and str(observed).strip() == value

        if not ax.AXUIElementSetAttributeValue(field, "AXValue", value):
            until = monotonic() + 1
            while monotonic() < until:
                if matches():
                    return
                sleep(0.05)
        if self.paste(pid, value, matches, field=field):
            return
        allowed = self.cg.CGPreflightPostEventAccess()
        raise RuntimeError(
            f"Focused Conductor model search ignored input (keyboard posting allowed: {allowed})"
        )


def _route(routes, workspace):
    for route in routes:
        if route.path.rstrip("/").endswith(f"/workspace/{workspace}"):
            return parse_qs(route.query).get("sessionId", [None])[0]
    return None


def _unique(nodes, role, label):
    found = [node for node, kind, title in nodes if kind == role and label(title)]
    return found[0] if len(found) == 1 else None


def _model_results(nodes, label, provider):
    pattern = re.compile(
        rf"^{re.escape(label)}\s+{re.escape(provider)}(?:\s|$)", re.IGNORECASE
    )
    return [
        node
        for node, role, name in nodes
        if role != "AXStaticText" and pattern.search(" ".join(name.split()))
    ]


def _quota_retry(nodes):
    error = ""
    candidate = None
    limit = re.compile(
        r"you(?:'|’)ve hit your (?:[0-9]+-hour|[0-9]+-day|weekly|daily) limit",
        re.IGNORECASE,
    )
    for node, role, name in nodes:
        if role == "AXStaticText" and name.startswith("Claude Code error:"):
            error = name
            candidate = None
        if role == "AXButton" and name == "Retry":
            candidate = node if limit.search(error) else None
            error = ""
    return candidate


class Handoff:
    LABELS: ClassVar[dict[str, str]] = {
        "sonnet-5-5-1m": "Sonnet 5.5",
        "opus-5-5-1m": "Opus 5.5",
        "gpt-6.2-sol": "GPT-6.2 Sol",
        "sonnet-5-1m": "Sonnet 5",
    }

    def __init__(self, desktop):
        self.desktop = desktop

    def _wait(self, workspace, condition, stage):
        until = monotonic() + 5
        while monotonic() < until:
            pid, nodes, routes = self.desktop.snapshot()
            session = _route(routes, workspace)
            if session and condition(session, nodes):
                return pid, session, nodes
            sleep(0.2)
        raise RuntimeError(
            f"Timed out at {stage}. Matched workspace route: {bool(session)}."
        )

    def _open_source(self, turn):
        UUID(turn.workspace_id)
        UUID(turn.session_id)
        self.desktop.require()
        subprocess.run(
            [
                "/usr/bin/open",
                f"conductor://workspace?id={turn.workspace_id}&session={turn.session_id}",
            ],
            timeout=5,
            check=True,
        )

    def _retry_button(self, turn):
        pid, _, nodes = self._wait(
            turn.workspace_id,
            lambda sid, nodes: (
                sid == turn.session_id and _quota_retry(nodes) is not None
            ),
            "finding Retry for the latest Claude quota error in the source chat",
        )
        return pid, _quota_retry(nodes)

    def prepare_retry(self, turn):
        self._open_source(turn)
        self._retry_button(turn)

    def submit_retry(self, turn):
        pid, button = self._retry_button(turn)
        if self.desktop.attribute(button, "AXEnabled") is not True:
            raise RuntimeError("Conductor has not enabled Retry")
        self.desktop.click_control(pid, button)

    def prepare(self, turn, model, title, marker):
        label = self.LABELS.get(model.model)
        if not label:
            raise RuntimeError("Unsupported model label for this Conductor version")
        self._open_source(turn)
        pid, _, _ = self._wait(
            turn.workspace_id,
            lambda sid, _: sid == turn.session_id,
            "opening the source chat",
        )
        self.desktop.key(pid, 17, self.desktop.cg.kCGEventFlagMaskCommand)

        pid, destination, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid != turn.session_id
                and any(name == "Add chat transcripts:" for _, _, name in ns)
            ),
            "opening a new tab with the transcript picker",
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
            self.desktop.key(pid, 44, self.desktop.cg.kCGEventFlagMaskCommand)
            field_roles = {"AXComboBox", "AXSearchField", "AXTextField"}

            def search_fields(ns):
                return [
                    node
                    for node, role, name in ns
                    if role in field_roles and "Search models" in name
                ]

            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: sid == destination and len(search_fields(ns)) == 1,
                "opening model search in the new tab",
            )
            self.desktop.type_search(pid, search_fields(nodes)[0], label)
            provider_label = {"claude": "Claude Code", "codex": "Codex"}[model.provider]
            pid, _, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid == destination
                    and len(_model_results(ns, label, provider_label)) == 1
                ),
                f"finding the {label} {provider_label} search result in the new tab",
            )
            self.desktop.click_control(
                pid, _model_results(nodes, label, provider_label)[0]
            )
            pid, destination, nodes = self._wait(
                turn.workspace_id,
                lambda sid, ns: (
                    sid != turn.session_id
                    and selected(ns) is not None
                    and any(name == "Add chat transcripts:" for _, _, name in ns)
                ),
                "verifying the model and effort in a fresh destination",
            )

        source = _unique(nodes, "AXButton", lambda name: name == title)
        if source is None:
            raise RuntimeError(
                "Source title is missing or duplicated in the transcript picker"
            )
        self.desktop.click_control(pid, source)
        attachments = {
            f"Transcript of {title}.md",
            f"session-transcript-{turn.session_id}.md",
        }

        def has_attachment(ns):
            return any(
                name.removeprefix("Remove ") in attachments
                and name.startswith("Remove ")
                for _, _, name in ns
            )

        pid, _, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: sid == destination and has_attachment(ns),
            "verifying the transcript attachment",
        )
        composer = _unique(
            nodes, "AXTextArea", lambda n: n == "What do you want to work on?"
        )
        if composer is None:
            raise RuntimeError("Destination composer is missing")
        value = self.desktop.attribute(composer, "AXValue") or ""
        if str(value).strip() not in attachments:
            raise RuntimeError("Destination contains an unexpected draft")
        if not self.desktop.wait_focus(pid, composer):
            self.desktop.ax.AXUIElementSetAttributeValue(composer, "AXFocused", True)
        if not self.desktop.wait_focus(pid, composer):
            self.desktop.click_control(pid, composer)
            if not self.desktop.wait_focus(pid, composer):
                raise RuntimeError("Conductor did not focus the destination composer")
        prompt = (
            " Read the attached transcript and continue the unfinished task. "
            "Preserve its constraints and check the current workspace and completed actions "
            f"before repeating any work. Recovery reference: {marker}."
        )
        if not self.desktop.paste(
            pid,
            prompt,
            lambda: marker in str(self.desktop.attribute(composer, "AXValue")),
            field=composer,
        ):
            raise RuntimeError("Conductor did not apply the continuation paste")
        self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid == destination
                and any(
                    kind == "AXTextArea"
                    and marker in str(self.desktop.attribute(node, "AXValue"))
                    for node, kind, _ in ns
                )
                and has_attachment(ns)
            ),
            "verifying the continuation draft",
        )
        return destination

    def submit(self, turn, destination, marker, title):
        attachments = {
            f"Remove Transcript of {title}.md",
            f"Remove session-transcript-{turn.session_id}.md",
        }
        pid, _, nodes = self._wait(
            turn.workspace_id,
            lambda sid, ns: (
                sid == destination
                and any(name in attachments for _, _, name in ns)
                and any(
                    kind == "AXTextArea"
                    and marker in str(self.desktop.attribute(node, "AXValue"))
                    for node, kind, _ in ns
                )
            ),
            "rechecking the marked destination before sending",
        )
        send = _unique(nodes, "AXButton", lambda name: name.startswith("Send message"))
        if send is None:
            raise RuntimeError("Cannot identify the destination Send button")
        if self.desktop.attribute(send, "AXEnabled") is not True:
            raise RuntimeError("Conductor has not enabled Send for the verified draft")
        self.desktop.click_control(pid, send)
