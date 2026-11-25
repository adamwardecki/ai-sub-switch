from datetime import UTC, datetime
from time import monotonic, sleep
from uuid import uuid4

from .desktop import DesktopUnavailable
from .rules import select_target


class UnlockCycle:
    def __init__(self):
        self.saw_lock = False
        self.complete = False

    def observe(self, state):
        if state == "locked":
            self.saw_lock = True
        elif state == "unlocked" and self.saw_lock:
            self.complete = True
        return self.complete and state == "unlocked"


def tick(
    queue,
    config,
    session,
    since,
    conductor,
    provider,
    desktop,
    handoff,
    live=False,
    codex=None,
):
    claude = tuple(a for a in config.accounts if a.provider == "claude")
    unlocked = desktop.unlocked()
    if unlocked:
        active = provider.active(claude)
        previous = queue.get("active")
        if active and (previous is None or active == previous):
            if previous is None:
                queue.set("active", active)
            if turn := conductor.blocked(session, since, active):
                queue.add(turn)
        elif active:
            queue.set("active", active)
        else:
            queue.set("active", "")
    elif (active := queue.get("active")) and (
        turn := conductor.blocked(session, since, active)
    ):
        queue.add(turn)
    if not unlocked:
        return "Waiting for unlock"

    pending = [turn for turn in queue.pending() if turn.session_id == session]
    for turn in pending:
        latest = conductor.blocked(turn.session_id, since, turn.account_id)
        if not latest or latest.turn_id != turn.turn_id:
            queue.transition(turn, "pending", "cancelled", "The source turn changed")
            continue
        if not active or active != turn.account_id:
            queue.transition(
                turn, "pending", "attention", "Active Claude account changed"
            )
            continue
        availability = provider.usage(claude)
        now = datetime.now(UTC)
        selection = select_target(turn, config, availability, now)
        if selection.kind == "wait" and codex:
            for account in config.accounts:
                if account.enabled and account.provider == "codex":
                    availability[account.id] = codex.usage(account)
            selection = select_target(turn, config, availability, datetime.now(UTC))
        if selection.kind in {"wait", "attention"}:
            if selection.kind == "attention" and live:
                queue.transition(turn, "pending", "attention", selection.reason)
            return selection.reason
        same_thread = (
            selection.model == turn.source and turn.source.provider == "claude"
        )
        if not live:
            action = (
                "retry in the same chat" if same_thread else "continue in a new chat"
            )
            return (
                f"Would {action} using {selection.account_id} / {selection.model.model}"
            )
        try:
            desktop.front()
        except DesktopUnavailable as exc:
            return str(exc)
        target = next(a for a in config.accounts if a.id == selection.account_id)
        if target.provider == "claude" and target.id != active and conductor.busy():
            return "Waiting for other Claude work to settle"
        title = None if same_thread else conductor.title(turn)
        marker = uuid4().hex
        if not queue.transition(turn, "pending", "preparing", marker):
            continue
        try:
            desktop.require()
            if same_thread:
                handoff.prepare_retry(turn)
                current = conductor.blocked(turn.session_id, since, turn.account_id)
                if not current or current.turn_id != turn.turn_id:
                    queue.transition(
                        turn, "preparing", "cancelled", "The source turn changed"
                    )
                    continue
            if target.provider == "claude" and target.id != active:
                if conductor.busy():
                    queue.transition(turn, "preparing", "pending", "Claude became busy")
                    return "Waiting for other Claude work to settle"
                provider.activate(target, claude)
                queue.set("active", target.id)
            if not same_thread:
                destination = handoff.prepare(turn, selection.model, title, marker)
            latest = conductor.blocked(turn.session_id, since, turn.account_id)
            if not latest or latest.turn_id != turn.turn_id:
                queue.transition(
                    turn, "preparing", "cancelled", "The source turn changed"
                )
                continue
            if same_thread:
                cursor = conductor.retry_cursor(turn.session_id)
                current = conductor.blocked(turn.session_id, since, turn.account_id)
                if not current or current.turn_id != turn.turn_id:
                    queue.transition(
                        turn, "preparing", "cancelled", "The source turn changed"
                    )
                    continue
                if not queue.transition(turn, "preparing", "retrying", str(cursor)):
                    return "Retry intent could not be recorded"
                handoff.submit_retry(turn)
            else:
                if not queue.transition(
                    turn, "preparing", "submitting", f"{destination}:{marker}"
                ):
                    return "Submission intent could not be recorded"
                handoff.submit(turn, destination, marker, title)
        except (RuntimeError, OSError, ValueError) as exc:
            queue.transition(turn, "preparing", "attention", str(exc))
            return f"Recovery needs attention: {exc}"
        return reconcile(queue, conductor, session, wait=True)
    return "No eligible blocked turn"


def reconcile(queue, conductor, session, wait=False):
    from types import SimpleNamespace

    for row in queue.status():
        state = row["state"]
        if row["session"] != session or state not in {"submitting", "retrying"}:
            continue
        turn = SimpleNamespace(session_id=session, turn_id=row["turn"])
        until = monotonic() + (6 if wait else 0)
        if state == "retrying":
            destination = session
            cursor = int(row["detail"])
        else:
            destination, marker = row["detail"].split(":", 1)
        while True:
            if state == "retrying":
                confirmed = conductor.retry_receipt(session, row["turn"], cursor)
            else:
                confirmed = conductor.receipt(destination, marker, session)
            if confirmed:
                queue.transition(turn, state, "verified", destination)
                return (
                    "Same-thread Retry recorded by Conductor"
                    if state == "retrying"
                    else "Continuation submitted and recorded by Conductor"
                )
            if monotonic() >= until:
                break
            sleep(0.2)
        return "Submission outcome is unconfirmed; inspect the chat before retrying"
    return None
