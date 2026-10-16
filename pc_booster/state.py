from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_NAME = "PC Booster"

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "en",
    "auto_pilot_enabled": True,
    "foreground_turbo": True,
    "background_ram_taming": True,
    "background_cpu_eco": True,
    "ram_tame_threshold_mb": 350,
    "ram_emergency_threshold_percent": 88,
    "close_to_tray": True,
    "start_at_login": False,
    "tray_notifications": True,
    "auto_memory_alert": True,
    "whitelist": [
        "steam.exe",
        "steamwebhelper.exe",
        "discord.exe",
        "spotify.exe",
        "obs64.exe",
        "code.exe",
        "devenv.exe",
        "photoshop.exe",
        "blender.exe",
        "epicgameslauncher.exe",
        "riotclientservices.exe",
    ],
}


def data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(name: str, default: Any) -> Any:
    path = data_dir() / name
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(name: str, value: Any) -> None:
    path = data_dir() / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def get_settings() -> dict[str, Any]:
    loaded = read_json("settings.json", {})
    if not isinstance(loaded, dict):
        loaded = {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(loaded)
    # Ensure whitelist is a list of strings
    if not isinstance(settings.get("whitelist"), list):
        settings["whitelist"] = list(DEFAULT_SETTINGS["whitelist"])
    else:
        settings["whitelist"] = [str(item).lower().strip() for item in settings["whitelist"] if item]
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    write_json("settings.json", settings)
