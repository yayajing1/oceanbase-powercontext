from __future__ import annotations

from pathlib import Path

import pytest

from paperdesk.config import Config
from paperdesk.errors import ConfigError


def test_defaults_match_technical_design():
    config = Config.from_env(env_file=Path("missing.env"), environ={})

    assert (config.db_host, config.db_port) == ("127.0.0.1", 2882)
    assert config.db_user == "root"
    assert config.db_name == "paperdesk"
    assert config.max_chars == 800


def test_process_environment_overrides_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERDESK_DB_HOST", "10.0.0.9")
    monkeypatch.setenv("PAPERDESK_DB_PORT", "2883")
    monkeypatch.setenv("PAPERDESK_DB_NAME", "custom_db")
    monkeypatch.setenv("PAPERDESK_MAX_CHARS", "500")

    config = Config.from_env(env_file=tmp_path / "missing.env")

    assert config.db_host == "10.0.0.9"
    assert config.db_port == 2883
    assert config.db_name == "custom_db"
    assert config.max_chars == 500


def test_dotenv_is_loaded_and_process_env_wins(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAPERDESK_DB_NAME=from_file\nPAPERDESK_MAX_CHARS=300\nPAPERDESK_DB_PASSWORD=\n",
        encoding="utf-8",
    )

    from_file = Config.from_env(env_file=env_file, environ={})
    assert from_file.db_name == "from_file"
    assert from_file.max_chars == 300

    monkeypatch.setenv("PAPERDESK_DB_NAME", "from_env")
    merged = Config.from_env(env_file=env_file)
    assert merged.db_name == "from_env"
    assert merged.max_chars == 300


def test_invalid_integer_is_actionable(tmp_path):
    with pytest.raises(ConfigError, match="PAPERDESK_MAX_CHARS"):
        Config.from_env(env_file=tmp_path / "missing.env", environ={"PAPERDESK_MAX_CHARS": "abc"})


def test_invalid_database_name_rejected(tmp_path):
    with pytest.raises(ConfigError, match="PAPERDESK_DB_NAME"):
        Config.from_env(env_file=tmp_path / "missing.env", environ={"PAPERDESK_DB_NAME": "bad name`"})


def test_redacted_hides_password():
    config = Config.from_env(
        env_file=Path("missing.env"),
        environ={"PAPERDESK_DB_PASSWORD": "secret-value"},
    )
    assert config.redacted()["db_password"] == "***set***"
    assert "secret-value" not in str(config.redacted())
