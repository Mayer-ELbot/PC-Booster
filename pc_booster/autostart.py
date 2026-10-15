from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "PC Booster"


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--background"]
    else:
        script = Path(sys.argv[0]).resolve()
        if not script.is_file():
            raise ValueError("Could not find the PC Booster entry point for startup.")
        command = [sys.executable, str(script), "--background"]
    encoded = subprocess.list2cmdline(command)
    if len(encoded) > 260:
        raise ValueError("The startup command exceeds Windows' 260-character limit.")
    return encoded


def set_enabled(enabled: bool) -> None:
    if enabled:
        command = _startup_command()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
