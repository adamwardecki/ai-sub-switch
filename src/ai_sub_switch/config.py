import os
import tomllib
from importlib.resources import files
from pathlib import Path

from .domain import Account, Config, ModelChoice, ModelMapping

PROVIDERS = {"claude", "codex"}


class ConfigError(ValueError):
    pass


def _table(value: object, allowed: set[str], location: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{location} must be a table")
    unknown = value.keys() - allowed
    if unknown:
        raise ConfigError(
            f"{location} has unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _list(value: object, location: str) -> list:
    if not isinstance(value, list):
        raise ConfigError(f"{location} must be an array")
    return value


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise ConfigError(
            f"{location} must be a nonempty string without surrounding whitespace"
        )
    return value


def _provider(row: dict, key: str) -> str:
    provider = _string(row.get(key), key)
    if provider not in PROVIDERS:
        raise ConfigError(f"{key} is unsupported")
    return provider


def _account(value: object) -> Account:
    row = _table(
        value, {"id", "label", "provider", "backend_ref", "enabled"}, "account"
    )
    enabled = row.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("account.enabled must be a boolean")
    account_id = _string(row.get("id"), "account.id")
    return Account(
        account_id,
        _string(row.get("label", account_id), "account.label"),
        _provider(row, "provider"),
        _string(row.get("backend_ref"), "account.backend_ref"),
        enabled,
    )


def _mapping(value: object, accounts: dict[str, Account]) -> ModelMapping:
    row = _table(
        value,
        {
            "source_provider",
            "source_model",
            "destination_provider",
            "destination_account",
            "target_model",
            "effort",
        },
        "mapping",
    )
    destination = _provider(row, "destination_provider")
    account_id = None
    if "destination_account" in row:
        account_id = _string(row["destination_account"], "mapping.destination_account")
        if account_id not in accounts or accounts[account_id].provider != destination:
            raise ConfigError(
                "mapping destination account is unknown or has a different provider"
            )
    effort = _string(row["effort"], "mapping.effort") if "effort" in row else None
    return ModelMapping(
        _provider(row, "source_provider"),
        _string(row.get("source_model"), "mapping.source_model"),
        destination,
        account_id,
        ModelChoice(
            destination,
            _string(row.get("target_model"), "mapping.target_model"),
            effort,
        ),
    )


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not load configuration: {exc}") from exc

    _table(
        data,
        {"schema_version", "order", "accounts", "mappings", "conductor_db"},
        "configuration",
    )
    version = data.get("schema_version")
    if type(version) is not int or version != 1:
        raise ConfigError("configuration requires schema_version = 1")

    accounts = {}
    for entry in _list(data.get("accounts"), "accounts"):
        account = _account(entry)
        if account.id in accounts:
            raise ConfigError("duplicate account ID")
        accounts[account.id] = account
    order = tuple(
        _string(value, "order entry") for value in _list(data.get("order"), "order")
    )
    if len(set(order)) != len(order):
        raise ConfigError("duplicate account in order")
    if set(order) - accounts.keys():
        raise ConfigError("unknown account in order")
    if {account.id for account in accounts.values() if account.enabled} - set(order):
        raise ConfigError("order is missing an enabled account")

    mappings = {}
    for entry in _list(data.get("mappings", []), "mappings"):
        mapping = _mapping(entry, accounts)
        key = (
            mapping.source_provider,
            mapping.source_model,
            mapping.destination_provider,
            mapping.destination_account,
        )
        if key in mappings:
            raise ConfigError("duplicate model mapping")
        mappings[key] = mapping

    default_db = "~/Library/Application Support/com.conductor.app/conductor.db"
    database = Path(
        _string(data.get("conductor_db", default_db), "conductor_db")
    ).expanduser()
    if not database.is_absolute():
        database = path.parent / database
    return Config(tuple(accounts.values()), order, tuple(mappings.values()), database)


def init_config(path: Path) -> None:
    resource = files("ai_sub_switch").joinpath("default-config.toml")
    if resource.is_file():
        template = resource.read_bytes()
    else:
        template = (
            Path(__file__).resolve().parents[2] / "examples/config.toml"
        ).read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ConfigError(f"Configuration already exists: {path}") from None
    with os.fdopen(fd, "wb") as stream:
        stream.write(template)
