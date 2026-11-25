from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_sub_switch.domain import (
    Account,
    Availability,
    BlockedTurn,
    Config,
    ModelChoice,
    ModelMapping,
)
from ai_sub_switch.rules import select_target

NOW = datetime(2026, 11, 25, 10, tzinfo=UTC)
CLAUDE = ModelChoice("claude", "sonnet-5-5-1m", "high")
CODEX = ModelChoice("codex", "gpt-6.2-sol", "high")
ACCOUNTS = tuple(
    Account(key, key, provider, key, True)
    for key, provider in (("a", "claude"), ("b", "claude"), ("openai", "codex"))
)
ORDER = ("a", "b", "openai")
MAPPING = ModelMapping("claude", CLAUDE.model, "codex", None, CODEX)
TURN = BlockedTurn("w", "s", "t", "e", "a", CLAUDE, NOW - timedelta(seconds=1), None)


def choose(states, mappings=(MAPPING,)):
    availability = {
        key: Availability(
            key,
            state,
            NOW + timedelta(hours=1) if state == "exhausted" else None,
            NOW,
            "",
        )
        for key, state in zip(ORDER, states, strict=True)
    }
    return select_target(
        TURN, Config(ACCOUNTS, ORDER, mappings, Path("conductor.db")), availability, NOW
    )


def test_first_available_account_keeps_current_claude_model():
    result = choose(("exhausted", "ready", "ready"))
    assert (result.kind, result.account_id, result.model) == ("handoff", "b", CLAUDE)


def test_codex_gets_explicit_model_mapping():
    result = choose(("exhausted", "exhausted", "ready"))
    assert (result.kind, result.account_id, result.model) == (
        "handoff",
        "openai",
        CODEX,
    )


def test_original_account_can_resume_when_it_recovers():
    assert choose(("ready", "ready", "ready")).kind == "resume"


def test_missing_mapping_and_unavailable_accounts():
    assert choose(("exhausted", "exhausted", "ready"), ()).kind == "attention"
    assert choose(("exhausted", "unknown", "exhausted")).retry_at == NOW + timedelta(
        seconds=60
    )


def test_stale_availability_cannot_trigger_a_switch():
    stale = Availability("b", "ready", None, NOW - timedelta(minutes=2), "")
    config = Config(ACCOUNTS, ORDER, (MAPPING,), Path("conductor.db"))
    result = select_target(TURN, config, {"b": stale}, NOW)
    assert result.kind == "wait"
    assert result.account_id is None


def test_account_mapping_overrides_same_provider_default():
    target = ModelChoice("claude", "opus-5-5-1m", "high")
    override = ModelMapping("claude", CLAUDE.model, "claude", "b", target)
    assert choose(("exhausted", "ready", "ready"), (MAPPING, override)).model == target


def test_disabled_target_is_skipped():
    a, b, codex = ACCOUNTS
    disabled = Account(b.id, b.label, b.provider, b.backend_ref, False)
    config = Config((a, disabled, codex), ORDER, (MAPPING,), Path("conductor.db"))
    status = {key: Availability(key, "ready", None, NOW, "") for key in ("b", "openai")}
    assert select_target(TURN, config, status, NOW).account_id == "openai"
