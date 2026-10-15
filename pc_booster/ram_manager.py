from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any

import psutil


WM_CLOSE = 0x0010
PROTECTED_PROCESSES = {
    "system",
    "registry",
    "system idle process",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "svchost.exe",
    "dwm.exe",
    "explorer.exe",
    "sihost.exe",
    "ctfmon.exe",
    "fontdrvhost.exe",
    "runtimebroker.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "lockapp.exe",
    "consent.exe",
    "taskhostw.exe",
    "taskmgr.exe",
    "msedgewebview2.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "searchhost.exe",
    "searchindexer.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "securityhealthservice.exe",
    "securityhealthsystray.exe",
    "msmpeng.exe",
    "nissrv.exe",
}


def _windows_api() -> tuple[Any, Any]:
    if os.name != "nt":
        raise OSError("The background app manager is available on Windows only.")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    return user32, enum_proc


def _visible_windows() -> dict[int, list[dict[str, Any]]]:
    user32, enum_proc = _windows_api()
    by_pid: dict[int, list[dict[str, Any]]] = {}

    def visit(handle: int, _extra: int) -> bool:
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(min(length + 1, 2049))
        if not user32.GetWindowTextW(handle, title, len(title)):
            return True
        pid = wintypes.DWORD()
        if user32.GetWindowThreadProcessId(handle, ctypes.byref(pid)) and pid.value:
            by_pid.setdefault(pid.value, []).append(
                {"handle": handle, "title": title.value}
            )
        return True

    callback = enum_proc(visit)
    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return by_pid


def list_background_apps() -> list[dict[str, Any]]:
    """List same-user apps with visible titled windows, excluding Windows essentials."""
    current_user = psutil.Process(os.getpid()).username().casefold()
    current_pid = os.getpid()
    windows = _visible_windows()
    apps: list[dict[str, Any]] = []
    attributes = ["pid", "name", "memory_info", "username", "create_time"]

    for process in psutil.process_iter(attributes):
        try:
            info = process.info
            pid = info["pid"]
            name = info["name"]
            username = info["username"]
            memory = info["memory_info"]
            handles = windows.get(pid, [])
            if (
                pid == current_pid
                or not name
                or name.casefold() in PROTECTED_PROCESSES
                or not username
                or username.casefold() != current_user
                or not handles
                or not memory
            ):
                continue
            apps.append(
                {
                    "pid": pid,
                    "name": name,
                    "memory": memory.rss,
                    "create_time": info["create_time"],
                    "title": handles[0]["title"],
                    "window_count": len(handles),
                }
            )
        except (
            KeyError,
            TypeError,
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            OSError,
        ):
            continue

    apps.sort(key=lambda item: item["memory"], reverse=True)
    return apps


def request_graceful_close(apps: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Send WM_CLOSE to selected app windows; never terminate a process forcibly."""
    user32, _enum_proc = _windows_api()
    current_user = psutil.Process(os.getpid()).username().casefold()
    windows = _visible_windows()
    requested: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for selected in apps:
        name = selected.get("name")
        pid = selected.get("pid")
        created = selected.get("create_time")
        if (
            not isinstance(name, str)
            or not isinstance(pid, int)
            or not isinstance(created, (float, int))
            or name.casefold() in PROTECTED_PROCESSES
            or pid == os.getpid()
        ):
            skipped.append(str(name or "Unknown app"))
            continue

        try:
            process = psutil.Process(pid)
            if (
                process.name().casefold() != name.casefold()
                or abs(process.create_time() - float(created)) > 0.01
                or process.username().casefold() != current_user
            ):
                skipped.append(name)
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            skipped.append(name)
            continue

        targets = windows.get(pid, [])
        if not targets:
            skipped.append(name)
            continue
        results = [
            bool(user32.PostMessageW(target["handle"], WM_CLOSE, 0, 0))
            for target in targets
        ]
        if all(results):
            requested.append(name)
        else:
            failed.append(name)

    return {"requested": requested, "skipped": skipped, "failed": failed}
