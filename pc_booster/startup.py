from __future__ import annotations

import winreg
from typing import Any

from .state import read_json, write_json


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ALLOWED_TYPES = {winreg.REG_SZ, winreg.REG_EXPAND_SZ}
MAX_COMMAND_LENGTH = 32760


def list_active() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, value_type = winreg.EnumValue(key, index)
                    index += 1
                except OSError:
                    break
                if name and value_type in ALLOWED_TYPES and isinstance(value, str):
                    entries.append({"name": name, "command": value, "type": value_type})
    except FileNotFoundError:
        return []
    return sorted(entries, key=lambda item: item["name"].casefold())


def list_disabled() -> list[dict[str, Any]]:
    saved = read_json("disabled_startup.json", {})
    if not isinstance(saved, dict):
        return []
    result = []
    for name, item in saved.items():
        if (
            isinstance(name, str)
            and isinstance(item, dict)
            and item.get("type") in ALLOWED_TYPES
            and isinstance(item.get("value"), str)
        ):
            result.append({"name": name, "command": item["value"]})
    return sorted(result, key=lambda item: item["name"].casefold())


def disable(name: str) -> None:
    if not isinstance(name, str) or not name or len(name) > 255:
        raise ValueError("The startup entry name is invalid.")

    saved = read_json("disabled_startup.json", {})
    if not isinstance(saved, dict):
        saved = {}
    if name in saved:
        raise ValueError("This entry is already disabled.")

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    ) as key:
        value, value_type = winreg.QueryValueEx(key, name)
        if value_type not in ALLOWED_TYPES or not isinstance(value, str):
            raise ValueError("This entry type is not supported for safe disabling.")
        if len(value) > MAX_COMMAND_LENGTH:
            raise ValueError("The startup command exceeds the allowed length.")

        saved[name] = {"type": value_type, "value": value}
        write_json("disabled_startup.json", saved)
        try:
            winreg.DeleteValue(key, name)
        except OSError:
            saved.pop(name, None)
            write_json("disabled_startup.json", saved)
            raise


def restore(name: str) -> None:
    saved = read_json("disabled_startup.json", {})
    if not isinstance(saved, dict) or name not in saved:
        raise ValueError("No restore backup was found for this entry.")
    item = saved[name]
    if (
        not isinstance(item, dict)
        or item.get("type") not in ALLOWED_TYPES
        or not isinstance(item.get("value"), str)
        or len(item["value"]) > MAX_COMMAND_LENGTH
    ):
        raise ValueError("The restore backup is invalid. The Registry was not changed.")

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    ) as key:
        try:
            existing, existing_type = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            winreg.SetValueEx(key, name, 0, item["type"], item["value"])
        else:
            if existing == item["value"] and existing_type == item["type"]:
                saved.pop(name, None)
                write_json("disabled_startup.json", saved)
                return
            raise ValueError("Another entry already uses this name. It was not overwritten.")

    saved.pop(name, None)
    write_json("disabled_startup.json", saved)
