"""Minimal root-level settings loader."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from majsoul.logger import logger


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "settings.json"
LEGACY_CONFIG_PATH = ROOT_DIR / "settings" / "settings.json"
DEFAULT_CONFIG = {
    "model_path": "mjai_bot/mortal/mortal_298k.pth",
    "autoplay_account": {
        "username": "",
        "password": "",
    },
}


@dataclass
class Account:
    username: str = ""
    password: str = ""


@dataclass
class Settings:
    model_path: str
    autoplay_account: Account

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Settings":
        data = normalize(raw)
        account = data["autoplay_account"]
        return cls(
            model_path=data["model_path"],
            autoplay_account=Account(
                username=account["username"],
                password=account["password"],
            ),
        )


def normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return data

    model_path = raw.get("model_path")
    if isinstance(model_path, str):
        data["model_path"] = model_path

    account = raw.get("autoplay_account")
    if isinstance(account, dict):
        username = account.get("username")
        password = account.get("password")
        if isinstance(username, str):
            data["autoplay_account"]["username"] = username
        if isinstance(password, str):
            data["autoplay_account"]["password"] = password

    return data


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        backup = path.with_suffix(path.suffix + ".bak")
        path.rename(backup)
        logger.warning(f"Backed up invalid settings file to {backup}: {exc}")
        return DEFAULT_CONFIG


def load_settings() -> Settings:
    if not CONFIG_PATH.exists() and LEGACY_CONFIG_PATH.exists():
        CONFIG_PATH.write_text(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info(f"Migrated settings to {CONFIG_PATH}")

    if not CONFIG_PATH.exists():
        save_settings(DEFAULT_CONFIG)
        logger.info(f"Created {CONFIG_PATH}")

    raw = _read_json(CONFIG_PATH)
    normalized = normalize(raw)
    if raw != normalized:
        save_settings(normalized)
        logger.info("Normalized settings.json")

    return Settings.from_dict(normalized)


def save_settings(raw: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(normalize(raw), indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


settings = load_settings()
