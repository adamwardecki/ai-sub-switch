from collections.abc import Mapping
from datetime import datetime, timedelta

from .domain import Account, Availability, BlockedTurn, Config, ModelChoice, Selection

FRESHNESS = timedelta(seconds=60)


def _mapped_model(
    source: ModelChoice, account: Account, config: Config
) -> ModelChoice | None:
    matches = [
        mapping
        for mapping in config.mappings
        if mapping.source_provider == source.provider
        and mapping.source_model == source.model
        and mapping.destination_provider == account.provider
    ]
    for scope in (account.id, None):
        for mapping in matches:
            if mapping.destination_account == scope:
                return mapping.target
    return source if source.provider == account.provider else None


def select_target(
    turn: BlockedTurn,
    config: Config,
    availability: Mapping[str, Availability],
    now: datetime,
) -> Selection:
    accounts = {account.id: account for account in config.accounts}
    source = accounts.get(turn.account_id) if turn.account_id else None
    if source is None or source.provider != turn.source.provider:
        return Selection("attention", None, None, None, "Source account is unknown")

    def fresh_status(account: Account) -> Availability | None:
        status = availability.get(account.id)
        if (
            status is not None
            and status.account_id == account.id
            and timedelta(0) <= now - status.observed_at <= FRESHNESS
        ):
            return status
        return None

    original = fresh_status(source)
    if (
        source.enabled
        and original
        and original.state == "ready"
        and original.observed_at > turn.observed_at
    ):
        return Selection(
            "resume", source.id, turn.source, None, "Original account recovered"
        )

    retry_times = []
    needs_login = False
    for account in (
        source,
        *(accounts[key] for key in config.order if key != source.id),
    ):
        if not account.enabled:
            continue
        status = fresh_status(account)
        if account.id == source.id and (
            status is None or status.observed_at <= turn.observed_at
        ):
            if turn.resets_at and turn.resets_at > now:
                retry_times.append(turn.resets_at)
                continue
            status = None
        if status is None or status.state == "unknown":
            retry_times.append(now + FRESHNESS)
        elif status.state == "exhausted":
            reset = status.resets_at
            retry_times.append(reset if reset and reset > now else now + FRESHNESS)
        elif status.state == "reauth":
            needs_login = True
        else:
            model = _mapped_model(turn.source, account, config)
            if model is None:
                return Selection(
                    "attention", account.id, None, None, "No destination model mapping"
                )
            return Selection(
                "handoff", account.id, model, None, "Next available account"
            )

    if retry_times:
        return Selection("wait", None, None, min(retry_times), "Waiting for capacity")
    reason = "Account sign-in required" if needs_login else "No enabled account"
    return Selection("attention", None, None, None, reason)
