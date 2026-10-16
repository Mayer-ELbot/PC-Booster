from __future__ import annotations

import winreg
from typing import Any

from .state import read_json, write_json


TWEAKS: dict[str, dict[str, Any]] = {
    "gamemode": {
        "label": "Enable Windows Game Mode",
        "description": "Optimizes the Windows process scheduler for games and prioritizes gaming threads.",
        "path": r"Software\Microsoft\GameBar",
        "value_name": "AllowAutoGameMode",
        "target": 1,
        "target_type": winreg.REG_DWORD,
    },
    "menushowdelay": {
        "label": "Instant Menu & Context Response (10ms)",
        "description": "Reduces desktop menu display delay from 400ms to 10ms for instantaneous right-click.",
        "path": r"Control Panel\Desktop",
        "value_name": "MenuShowDelay",
        "target": "10",
        "target_type": winreg.REG_SZ,
    },
    "backgroundapps": {
        "label": "Disable Background UWP Apps",
        "description": "Prevents idle Windows Store/UWP apps from quietly consuming CPU in the background.",
        "path": r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
        "value_name": "GlobalUserDisabled",
        "target": 1,
        "target_type": winreg.REG_DWORD,
    },
    "bingsearch": {
        "label": "Disable Bing Suggestions in Start Search",
        "description": "Stops slow web suggestions in Start Menu, making local app searching instantaneous.",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Search",
        "value_name": "BingSearchEnabled",
        "target": 0,
        "target_type": winreg.REG_DWORD,
    },
    "animations": {
        "label": "Reduce minimize and maximize animations",
        "description": "Reduces window minimize/maximize animation time for snappy desktop navigation.",
        "path": r"Control Panel\Desktop\WindowMetrics",
        "value_name": "MinAnimate",
        "target": "0",
        "target_type": winreg.REG_SZ,
    },
    "transparency": {
        "label": "Turn off Windows transparency effects",
        "description": "Disables GPU-heavy acrylic transparency across the taskbar and system windows.",
        "path": r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "value_name": "EnableTransparency",
        "target": 0,
        "target_type": winreg.REG_DWORD,
    },
}


def _read_value(path: str, value_name: str) -> dict[str, Any]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, value_type = winreg.QueryValueEx(key, value_name)
            return {"exists": True, "type": value_type, "value": value}
    except FileNotFoundError:
        return {"exists": False}


def _write_value(path: str, value_name: str, value_type: int, value: Any) -> None:
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, value_name, 0, value_type, value)


def _restore_value(path: str, value_name: str, state: dict[str, Any]) -> None:
    if state.get("exists") is True:
        value_type = state.get("type")
        value = state.get("value")
        if value_type not in {winreg.REG_SZ, winreg.REG_DWORD, winreg.REG_EXPAND_SZ}:
            raise ValueError("The saved value type is not supported.")
        if isinstance(value, str) and len(value) > 32760:
            raise ValueError("The saved value exceeds the allowed length.")
        if not isinstance(value, (str, int)):
            raise ValueError("The saved value is invalid.")
        _write_value(path, value_name, value_type, value)
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def current_states() -> dict[str, bool]:
    result = {}
    for key, item in TWEAKS.items():
        value = _read_value(item["path"], item["value_name"])
        result[key] = value.get("exists") is True and str(value.get("value")) == str(item["target"])
    return result


def apply_selected(names: list[str]) -> list[str]:
    backups = read_json("tweak_backup.json", {})
    if not isinstance(backups, dict):
        backups = {}
    applied: list[str] = []
    for name in names:
        if name not in TWEAKS:
            continue
        item = TWEAKS[name]
        backup_key = name
        if backup_key not in backups:
            backups[backup_key] = _read_value(item["path"], item["value_name"])
            write_json("tweak_backup.json", backups)
        _write_value(item["path"], item["value_name"], item["target_type"], item["target"])
        applied.append(name)
    return applied


def restore_original() -> list[str]:
    backups = read_json("tweak_backup.json", {})
    if not isinstance(backups, dict):
        return []
    restored: list[str] = []
    failures: dict[str, Any] = {}
    for name, state in backups.items():
        item = TWEAKS.get(name)
        if item is None or not isinstance(state, dict):
            continue
        try:
            _restore_value(item["path"], item["value_name"], state)
            restored.append(name)
        except (OSError, ValueError, TypeError):
            failures[name] = state
    write_json("tweak_backup.json", failures)
    return restored
