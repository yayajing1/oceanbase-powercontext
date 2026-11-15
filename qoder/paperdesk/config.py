"""配置读取：进程环境变量优先，其次 qoder/.env，最后默认值。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import ConfigError

__all__ = ["Config", "PACKAGE_ROOT", "QODER_ROOT", "DEFAULT_ENV_FILE"]

PACKAGE_ROOT = Path(__file__).resolve().parent
QODER_ROOT = PACKAGE_ROOT.parent
DEFAULT_ENV_FILE = QODER_ROOT / ".env"

_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_$-]{1,64}$")


def _read_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 形式的 .env；不支持变量展开，保持零依赖。"""
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"无法读取环境文件 {path}：{exc}") from exc

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def _int_from(env: dict[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值为 {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} 必须 >= {minimum}，当前值为 {value}")
    return value


def _str_from(env: dict[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    return default if raw is None else raw


@dataclass(frozen=True)
class Config:
    """运行配置。字段与 PAPERDESK_* 环境变量一一对应。"""

    db_host: str = "127.0.0.1"
    db_port: int = 2882
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "paperdesk"
    db_connect_timeout: int = 10
    max_chars: int = 800
    env_file: Path = DEFAULT_ENV_FILE

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "Config":
        """按「进程环境 > .env > 默认值」构造配置。"""
        env = dict(os.environ) if environ is None else dict(environ)

        requested = env.get("PAPERDESK_ENV_FILE")
        if env_file is None:
            env_file = Path(requested).expanduser() if requested else DEFAULT_ENV_FILE
        else:
            env_file = Path(env_file).expanduser()

        for key, value in _read_env_file(env_file).items():
            env.setdefault(key, value)

        db_name = _str_from(env, "PAPERDESK_DB_NAME", "paperdesk").strip()
        if not _DB_NAME_RE.match(db_name):
            raise ConfigError(
                "PAPERDESK_DB_NAME 只能包含字母、数字、下划线、$ 或 -，"
                f"当前值为 {db_name!r}"
            )

        return cls(
            db_host=_str_from(env, "PAPERDESK_DB_HOST", "127.0.0.1").strip(),
            db_port=_int_from(env, "PAPERDESK_DB_PORT", 2882),
            db_user=_str_from(env, "PAPERDESK_DB_USER", "root").strip(),
            db_password=_str_from(env, "PAPERDESK_DB_PASSWORD", ""),
            db_name=db_name,
            db_connect_timeout=_int_from(env, "PAPERDESK_DB_CONNECT_TIMEOUT", 10),
            max_chars=_int_from(env, "PAPERDESK_MAX_CHARS", 800),
            env_file=env_file,
        )

    def with_overrides(self, **overrides: object) -> "Config":
        for key, value in overrides.items():
            if value is None:
                continue
            if not hasattr(self, key):
                raise ConfigError(f"未知配置项：{key}")
            if key == "db_name" and not _DB_NAME_RE.match(str(value)):
                raise ConfigError(f"PAPERDESK_DB_NAME 不合法：{value!r}")
            if key in ("db_port", "db_connect_timeout", "max_chars") and int(value) < 1:
                raise ConfigError(f"{key} 必须 >= 1，当前值为 {value!r}")
        return replace(self, **overrides)  # type: ignore[arg-type]

    @property
    def db_target(self) -> str:
        return f"{self.db_user}@{self.db_host}:{self.db_port}/{self.db_name}"

    def redacted(self) -> dict[str, object]:
        return {
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_user": self.db_user,
            "db_name": self.db_name,
            "db_password": "***set***" if self.db_password else "***empty***",
            "max_chars": self.max_chars,
            "env_file": str(self.env_file),
        }
