from datetime import UTC, datetime
from pathlib import Path

from ai_sub_switch.domain import BlockedTurn, ModelChoice
from ai_sub_switch.recovery import Queue, quota_failure


def test_quota_requires_rejection_and_root_failure():
    quota = {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "rateLimitType": "five_hour",
            "resetsAt": 1795616400,
        },
    }
    error = {"type": "assistant", "error": "rate_limit", "api_error_status": 429}
    assert quota_failure([quota]) is None
    assert quota_failure([quota, {**error, "parent_tool_use_id": "child"}]) is None
    assert quota_failure([quota, error]) == datetime.fromtimestamp(1795616400, UTC)


def test_queue_persists_uncertain_submission_without_replay(tmp_path: Path):
    path = tmp_path / "queue.sqlite"
    turn = BlockedTurn(
        "w",
        "s",
        "t",
        "e",
        "a",
        ModelChoice("claude", "opus", None),
        datetime.now(UTC),
        None,
    )
    with Queue(path) as queue:
        queue.add(turn)
        queue.add(turn)
        assert len(queue.pending()) == 1
        assert queue.transition(turn, "pending", "submitting")
    with Queue(path) as queue:
        assert not queue.pending()
        assert queue.status()[0]["state"] == "submitting"
        assert not queue.transition(turn, "pending", "submitting")


def test_locked_recovery_queues_then_rechecks_source(tmp_path):
    from types import SimpleNamespace

    from ai_sub_switch.coordinator import tick
    from ai_sub_switch.domain import Account, Availability, Config

    now = datetime.now(UTC)
    turn = BlockedTurn(
        "w", "s", "t", "e", "a", ModelChoice("claude", "opus", None), now, None
    )
    accounts = (
        Account("a", "A", "claude", "a", True),
        Account("b", "B", "claude", "b", True),
    )
    config = Config(accounts, ("a", "b"), (), Path("unused"))
    unlocked = False
    source = turn
    calls = []
    desktop = SimpleNamespace(
        unlocked=lambda: unlocked, require=lambda: None, front=lambda: 1
    )
    conductor = SimpleNamespace(
        blocked=lambda *_: source,
        busy=lambda: False,
        title=lambda _: "Test",
        receipt=lambda *_: True,
    )
    provider = SimpleNamespace(
        active=lambda _: "a",
        usage=lambda _: {"b": Availability("b", "ready", None, datetime.now(UTC), "")},
        activate=lambda target, _: calls.append(target.id),
    )
    handoff = SimpleNamespace(
        prepare=lambda *_: "dst", submit=lambda *_: calls.append("send")
    )
    with Queue(tmp_path / "state") as queue:
        queue.set("active", "a")
        tick(queue, config, "s", now, conductor, provider, desktop, handoff, True)
        assert len(queue.pending()) == 1
        assert calls == []
        unlocked = True
        source = None
        tick(queue, config, "s", now, conductor, provider, desktop, handoff, True)
        assert queue.status()[0]["state"] == "cancelled"
        assert calls == []


def test_blocked_turn_resumes_once_after_unlock(tmp_path):
    from types import SimpleNamespace

    from ai_sub_switch.coordinator import tick
    from ai_sub_switch.domain import Account, Availability, Config

    now = datetime.now(UTC)
    turn = BlockedTurn(
        "w", "s", "t", "e", "a", ModelChoice("claude", "opus", None), now, None
    )
    accounts = (
        Account("a", "A", "claude", "a", True),
        Account("b", "B", "claude", "b", True),
    )
    config = Config(accounts, ("a", "b"), (), Path("unused"))
    unlocked = False
    calls = []
    desktop = SimpleNamespace(
        unlocked=lambda: unlocked, require=lambda: None, front=lambda: 1
    )
    conductor = SimpleNamespace(
        blocked=lambda *_: turn,
        busy=lambda: False,
        title=lambda _: "Test",
        retry_cursor=lambda _: 42,
        retry_receipt=lambda session, source, cursor: (
            (session, source, cursor) == ("s", "t", 42) and "retry" in calls
        ),
    )
    provider = SimpleNamespace(
        active=lambda _: "b" if "b" in calls else "a",
        usage=lambda _: {"b": Availability("b", "ready", None, datetime.now(UTC), "")},
        activate=lambda target, _: calls.append(target.id),
    )
    handoff = SimpleNamespace(
        prepare_retry=lambda _: calls.append("locate"),
        submit_retry=lambda _: calls.append("retry"),
    )
    path = tmp_path / "state"
    with Queue(path) as queue:
        queue.set("active", "a")
        assert (
            tick(queue, config, "s", now, conductor, provider, desktop, handoff, True)
            == "Waiting for unlock"
        )
        assert calls == []
    unlocked = True
    with Queue(path) as queue:
        tick(queue, config, "s", now, conductor, provider, desktop, handoff, True)
        tick(queue, config, "s", now, conductor, provider, desktop, handoff, True)
        assert calls == ["locate", "b", "retry"]
        assert queue.status()[0]["state"] == "verified"


def test_receipt_needs_marker_and_matching_transcript_in_one_message(tmp_path):
    import sqlite3

    from ai_sub_switch.recovery import Conductor

    path = tmp_path / "conductor.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE session_messages (session_id, role, sent_at, content, full_message)"
        )
        db.executemany(
            "INSERT INTO session_messages VALUES (?, 'user', 1, ?, '')",
            [("dst", "marker"), ("dst", "session-transcript-source.md")],
        )
    conductor = Conductor(path)
    assert not conductor.receipt("dst", "marker", "source")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO session_messages VALUES ('dst', 'user', 1, ?, '')",
            ("marker session-transcript-source.md",),
        )
    assert conductor.receipt("dst", "marker", "source")


def test_model_search_result_ignores_static_labels_and_other_providers():
    from ai_sub_switch.desktop import _model_results

    nodes = [
        (1, "AXStaticText", "GPT-6.2 Sol Codex"),
        (2, "AXMenuItem", " GPT-6.2 Sol\nCodex Opens in new chat "),
        (3, "AXMenuItem", "GPT-6.2 Sol Other"),
        (4, "AXMenuItem", "GPT-6.2 Sol Mini Codex"),
    ]
    assert _model_results(nodes, "GPT-6.2 Sol", "Codex") == [2]


def test_retry_receipt_requires_new_sent_replay_in_same_thread(tmp_path):
    import json
    import sqlite3

    from ai_sub_switch.recovery import Conductor

    path = tmp_path / "conductor.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE session_events (session_id, session_index, event_type, content, reverted_at)"
        )

        def add(index, turn, text="original", state="sent", resume="s"):
            db.execute(
                "INSERT INTO session_events VALUES ('s', ?, 'userMessage', ?, NULL)",
                (
                    index,
                    json.dumps(
                        {
                            "turnId": turn,
                            "message": text,
                            "state": state,
                            "resume": resume,
                        }
                    ),
                ),
            )

        add(10, "old")
        add(11, "historic")
        add(13, "different", text="something else")
        add(14, "queued", state="queued")
    conductor = Conductor(path)
    assert conductor.retry_cursor("s") == 14
    assert not conductor.retry_receipt("s", "old", 12)
    with sqlite3.connect(path) as db:
        add(15, "retry")
    assert conductor.retry_receipt("s", "old", 12)


def test_select_latest_retry_only_if_its_error_is_a_subscription_failure():
    from ai_sub_switch.desktop import _quota_retry

    events = [
        (1, "AXStaticText", "Claude Code error: You've hit your 5-hour limit"),
        (2, "AXButton", "Retry"),
        (3, "AXButton", "Retry in new chat"),
        (4, "AXStaticText", "Claude Code error: You've hit your 7-day limit"),
        (5, "AXButton", "Retry"),
    ]
    assert _quota_retry(events) == 5
    assert (
        _quota_retry(
            events
            + [
                (6, "AXStaticText", "Claude Code error: Server unavailable"),
                (7, "AXButton", "Retry"),
            ]
        )
        is None
    )


def test_unlock_test_ignores_startup_and_ambiguous_desktop_state():
    from ai_sub_switch.coordinator import UnlockCycle

    gate = UnlockCycle()
    assert [
        gate.observe(state)
        for state in (
            "unlocked",
            "unknown",
            "unlocked",
            "locked",
            "unknown",
            "locked",
            "unlocked",
        )
    ] == [False, False, False, False, False, False, True]
