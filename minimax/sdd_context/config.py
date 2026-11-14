"""Configuration of the SDD context module.

Every value can be overridden through an environment variable so that the
module can be driven from a shell script or a CI job without editing code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Config", "load_config", "PACKAGE_ROOT"]

PACKAGE_ROOT = Path(__file__).resolve().parent
MINIMAX_ROOT = PACKAGE_ROOT.parent


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - configuration error path
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass
class Config:
    """Runtime configuration."""

    # --- seekdb / OceanBase ---------------------------------------------
    db_host: str = "127.0.0.1"
    db_port: int = 2881
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "sdd_context"
    db_connect_timeout: int = 10

    # --- the business project -------------------------------------------
    project_root: Path = field(default_factory=lambda: MINIMAX_ROOT / "demo_project")

    # --- PowerContext ----------------------------------------------------
    state_db: Path = field(default_factory=lambda: MINIMAX_ROOT / ".state" / "powercontext.db")
    scheduler_db: Path = field(default_factory=lambda: MINIMAX_ROOT / ".state" / "scheduler.db")
    scope_title: str = "SDD context (MiniMax x OceanBase)"
    scope_key: str = "sdd-context"

    # --- MiniMax ---------------------------------------------------------
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/anthropic"
    minimax_model: str = "MiniMax-M3"
    minimax_timeout: int = 300
    minimax_max_tokens: int = 32768
    minimax_thinking: str = "adaptive"

    # --- retrieval -------------------------------------------------------
    context_limit_per_kind: int = 20

    def ensure_state_dir(self) -> None:
        self.state_db.parent.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict[str, object]:
        """A dict safe to print - the API key is never included."""
        data = {
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_user": self.db_user,
            "db_name": self.db_name,
            "project_root": str(self.project_root),
            "state_db": str(self.state_db),
            "scope_key": self.scope_key,
            "minimax_base_url": self.minimax_base_url,
            "minimax_model": self.minimax_model,
            "minimax_api_key": "***set***" if self.minimax_api_key else "***missing***",
        }
        return data


def load_config(**overrides: object) -> Config:
    """Build a :class:`Config` from the environment.

    Keyword arguments override the environment derived values.
    """
    config = Config(
        db_host=_env("SDD_DB_HOST", "127.0.0.1"),
        db_port=_env_int("SDD_DB_PORT", 2881),
        db_user=_env("SDD_DB_USER", "root"),
        db_password=_env("SDD_DB_PASSWORD", ""),
        db_name=_env("SDD_DB_NAME", "sdd_context"),
        db_connect_timeout=_env_int("SDD_DB_CONNECT_TIMEOUT", 10),
        project_root=_env_path("SDD_PROJECT_ROOT", MINIMAX_ROOT / "demo_project"),
        state_db=_env_path("SDD_STATE_DB", MINIMAX_ROOT / ".state" / "powercontext.db"),
        scheduler_db=_env_path("SDD_SCHEDULER_DB", MINIMAX_ROOT / ".state" / "scheduler.db"),
        scope_title=_env("SDD_SCOPE_TITLE", "SDD context (MiniMax x OceanBase)"),
        scope_key=_env("SDD_SCOPE_KEY", "sdd-context"),
        minimax_api_key=_env("MINIMAX_API_KEY", ""),
        minimax_base_url=_env("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic"),
        minimax_model=_env("MINIMAX_MODEL", "MiniMax-M3"),
        minimax_timeout=_env_int("MINIMAX_TIMEOUT", 300),
        minimax_max_tokens=_env_int("MINIMAX_MAX_TOKENS", 32768),
        minimax_thinking=_env("MINIMAX_THINKING", "adaptive"),
        context_limit_per_kind=_env_int("SDD_CONTEXT_LIMIT", 20),
    )
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, value)
    return config
