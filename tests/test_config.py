from pathlib import Path

import pytest

from ai_sub_switch.config import ConfigError, load_config

EXAMPLE = Path(__file__).parents[1] / "examples" / "config.toml"


def test_example_encodes_two_claude_accounts_and_codex():
    config = load_config(EXAMPLE)
    assert config.order == ("claude-a", "claude-b", "codex")
    assert [account.provider for account in config.accounts] == [
        "claude",
        "claude",
        "codex",
    ]
    assert any(mapping.target.provider == "codex" for mapping in config.mappings)


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ('"claude-b", "codex"', '"claude-a", "codex"', "duplicate"),
        ('"claude-b", "codex"', '"typo", "codex"', "unknown"),
        ('provider = "codex"', 'provider = "unsupported"', "unsupported"),
        ("schema_version = 1", 'schema_version = 1\napi_key = "test"', "unknown"),
    ],
)
def test_invalid_configuration_reports_the_problem(tmp_path, old, new, reason):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError, match=reason):
        load_config(path)


def test_init_writes_a_private_editable_config_once(tmp_path):
    from ai_sub_switch.cli import main

    path = tmp_path / "config" / "accounts.toml"
    assert main(["--config", str(path), "init"]) == 0
    assert load_config(path).order == ("claude-a", "claude-b", "codex")
    assert path.stat().st_mode & 0o777 == 0o600

    path.write_text("user configuration")
    assert main(["--config", str(path), "init"]) != 0
    assert path.read_text() == "user configuration"
