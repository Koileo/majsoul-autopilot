import dataclasses
import json
from pathlib import Path

import jsonschema
from jsonschema.exceptions import ValidationError

from .logger import logger

FILE_PATH = Path(__file__).resolve().parent


@dataclasses.dataclass
class AutoplayTimeConfig:
    rand_min: float
    rand_max: float


@dataclasses.dataclass
class AutoplayAccountConfig:
    username: str
    password: str


@dataclasses.dataclass
class AutoplayModeConfig:
    type: str
    room: str


@dataclasses.dataclass
class Settings:
    model_path: str
    autoplay_time: AutoplayTimeConfig
    autoplay_account: AutoplayAccountConfig
    autoplay_mode: AutoplayModeConfig

    def update(self, raw: dict) -> None:
        normalized = _normalize_settings(raw)
        self.model_path = normalized["model_path"]
        self.autoplay_time = AutoplayTimeConfig(**normalized["autoplay_time"])
        self.autoplay_account = AutoplayAccountConfig(**normalized["autoplay_account"])
        self.autoplay_mode = AutoplayModeConfig(**normalized["autoplay_mode"])
        self.save()

    def save(self) -> None:
        with open(FILE_PATH / "settings.json", "w") as f:
            json.dump(_settings_to_dict(self), f, indent=4)
        logger.info(f"Saved settings to {FILE_PATH / 'settings.json'}")


def _default_settings() -> dict:
    return {
        "model_path": "mjai_bot/mortal/mortal.pth",
        "autoplay_account": {
            "username": "",
            "password": "",
        },
        "autoplay_mode": {
            "type": "4p_south",
            "room": "gold",
        },
        "autoplay_time": {
            "rand_min": 1.0,
            "rand_max": 3.0,
        },
    }


def _normalize_settings(raw: dict) -> dict:
    data = _default_settings()
    if not isinstance(raw, dict):
        return data

    for key in ("model_path",):
        if key in raw:
            data[key] = raw[key]

    if isinstance(raw.get("autoplay_account"), dict):
        account = raw["autoplay_account"]
        data["autoplay_account"] = {
            "username": account.get("username", ""),
            "password": account.get("password", ""),
        }

    if isinstance(raw.get("autoplay_mode"), dict):
        mode = raw["autoplay_mode"]
        data["autoplay_mode"] = {
            "type": mode.get("type", data["autoplay_mode"]["type"]),
            "room": mode.get("room", data["autoplay_mode"]["room"]),
        }

    if isinstance(raw.get("autoplay_time"), dict):
        timing = raw["autoplay_time"]
        data["autoplay_time"] = {
            "rand_min": timing.get("rand_min", data["autoplay_time"]["rand_min"]),
            "rand_max": timing.get("rand_max", data["autoplay_time"]["rand_max"]),
        }

    return data


def _settings_to_dict(settings: Settings) -> dict:
    return {
        "model_path": settings.model_path,
        "autoplay_account": dataclasses.asdict(settings.autoplay_account),
        "autoplay_mode": dataclasses.asdict(settings.autoplay_mode),
        "autoplay_time": dataclasses.asdict(settings.autoplay_time),
    }


def _parse_settings(raw: dict) -> Settings:
    data = _normalize_settings(raw)
    jsonschema.validate(data, get_schema())
    return Settings(
        model_path=data["model_path"],
        autoplay_time=AutoplayTimeConfig(**data["autoplay_time"]),
        autoplay_account=AutoplayAccountConfig(**data["autoplay_account"]),
        autoplay_mode=AutoplayModeConfig(**data["autoplay_mode"]),
    )


def load_settings() -> Settings:
    settings_path = FILE_PATH / "settings.json"
    if not settings_path.exists():
        with open(settings_path, "w") as f:
            json.dump(_default_settings(), f, indent=4)
        logger.info(f"Created new settings.json with default values")

    try:
        with open(settings_path, "r") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        logger.error(f"settings.json corrupted: {exc}")
        backup_path = FILE_PATH / "settings.json.bak"
        settings_path.rename(backup_path)
        logger.warning(f"Backed up settings.json to {backup_path}")
        raw = _default_settings()
        with open(settings_path, "w") as f:
            json.dump(raw, f, indent=4)

    normalized = _normalize_settings(raw)
    if raw != normalized:
        with open(settings_path, "w") as f:
            json.dump(normalized, f, indent=4)
        logger.info("Normalized settings.json to minimal pure-protocol settings")

    return _parse_settings(normalized)


def get_schema() -> dict:
    with open(FILE_PATH / "settings.schema.json", "r") as f:
        return json.load(f)


def get_settings() -> dict:
    with open(FILE_PATH / "settings.json", "r") as f:
        return _normalize_settings(json.load(f))


def save_settings(settings: dict) -> None:
    normalized = _normalize_settings(settings)
    jsonschema.validate(normalized, get_schema())
    with open(FILE_PATH / "settings.json", "w") as f:
        json.dump(normalized, f, indent=4)


def verify_settings(settings: dict) -> bool:
    try:
        jsonschema.validate(_normalize_settings(settings), get_schema())
        return True
    except ValidationError as exc:
        logger.error(f"Settings validation error: {exc.message}")
        return False


settings: Settings = load_settings()
