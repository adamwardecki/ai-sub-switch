from datetime import UTC, datetime
from time import monotonic, sleep
from uuid import uuid4

from .desktop import DesktopUnavailable
from .rules import select_target


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
        if not live:
            return (
                f"Would continue using {selection.account_id} / {selection.model.model}"
            )
        try:
            desktop.front()
        except DesktopUnavailable as exc:
            return str(exc)
        target = next(a for a in config.accounts if a.id == selection.account_id)
        if target.provider == "claude" and target.id != active and conductor.busy():
            return "Waiting for other Claude work to settle"
        title = conductor.title(turn)
        marker = uuid4().hex
        if not queue.transition(turn, "pending", "preparing", marker):
            continue
        try:
            desktop.require()
            if target.provider == "claude" and target.id != active:
                if conductor.busy():
                    queue.transition(turn, "preparing", "pending", "Claude became busy")
                    return "Waiting for other Claude work to settle"
                provider.activate(target, claude)
                queue.set("active", target.id)
            destination = handoff.prepare(turn, selection.model, title, marker)
            latest = conductor.blocked(turn.session_id, since, turn.account_id)
            if not latest or latest.turn_id != turn.turn_id:
                queue.transition(
                    turn, "preparing", "cancelled", "The source turn changed"
                )
                continue
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
    for row in queue.status():
        if row["session"] != session or row["state"] != "submitting":
            continue
        from types import SimpleNamespace

        destination, marker = row["detail"].split(":", 1)
        until = monotonic() + (6 if wait else 0)
        while True:
            if conductor.receipt(destination, marker, session):
                turn = SimpleNamespace(session_id=row["session"], turn_id=row["turn"])
                queue.transition(turn, "submitting", "verified", destination)
                return "Continuation submitted and recorded by Conductor"
            if monotonic() >= until:
                break
            sleep(0.2)
        return (
            "Submission outcome is unconfirmed; inspect the destination before retrying"
        )
    return None
