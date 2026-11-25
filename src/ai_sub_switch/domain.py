from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    provider: str
    backend_ref: str
    enabled: bool


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str
    effort: str | None


AvailabilityState = Literal["ready", "exhausted", "reauth", "unknown"]


@dataclass(frozen=True)
class Availability:
    account_id: str
    state: AvailabilityState
    resets_at: datetime | None
    observed_at: datetime
    reason: str


@dataclass(frozen=True)
class BlockedTurn:
    workspace_id: str
    session_id: str
    turn_id: str
    event_id: str
    account_id: str | None
    source: ModelChoice
    observed_at: datetime
    resets_at: datetime | None


@dataclass(frozen=True)
class ModelMapping:
    source_provider: str
    source_model: str
    destination_provider: str
    destination_account: str | None
    target: ModelChoice


@dataclass(frozen=True)
class Config:
    accounts: tuple[Account, ...]
    order: tuple[str, ...]
    mappings: tuple[ModelMapping, ...]
    conductor_db: Path


@dataclass(frozen=True)
class Selection:
    kind: Literal["resume", "handoff", "wait", "attention"]
    account_id: str | None
    model: ModelChoice | None
    retry_at: datetime | None
    reason: str
