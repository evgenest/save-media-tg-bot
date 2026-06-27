from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_REQUIRED_VARS = ("API_ID", "API_HASH", "BOT_TOKEN", "ALLOWED_USER_IDS", "STORAGE_DIR")
_DEFAULT_BATCH_TIMEOUT = "30"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    allowed_user_ids: "frozenset[int]"
    storage_dir: Path
    batch_timeout: float


def load_config(env: Mapping[str, str]) -> Config:
    missing = [name for name in _REQUIRED_VARS if not env.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    try:
        api_id = int(env["API_ID"])
    except ValueError as exc:
        raise ConfigError("API_ID must be an integer") from exc

    try:
        allowed_user_ids = frozenset(
            int(part.strip())
            for part in env["ALLOWED_USER_IDS"].split(",")
            if part.strip()
        )
    except ValueError as exc:
        raise ConfigError(
            "ALLOWED_USER_IDS must be a comma-separated list of integers"
        ) from exc

    if not allowed_user_ids:
        raise ConfigError("ALLOWED_USER_IDS must contain at least one user ID")

    try:
        batch_timeout = float(env.get("BATCH_TIMEOUT", _DEFAULT_BATCH_TIMEOUT))
    except ValueError as exc:
        raise ConfigError("BATCH_TIMEOUT must be a number") from exc

    if batch_timeout <= 0:
        raise ConfigError("BATCH_TIMEOUT must be greater than zero")

    return Config(
        api_id=api_id,
        api_hash=env["API_HASH"],
        bot_token=env["BOT_TOKEN"],
        allowed_user_ids=allowed_user_ids,
        storage_dir=Path(env["STORAGE_DIR"]),
        batch_timeout=batch_timeout,
    )
