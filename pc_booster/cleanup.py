from __future__ import annotations

import ctypes
import os
import stat
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from time import time


MAX_PREVIEW_FILES = 1000
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class ScanResult:
    files: list[tuple[str, int]]
    truncated: bool
    scanned_root: str

    @property
    def bytes_total(self) -> int:
        return sum(size for _, size in self.files)


def _is_reparse(path: str) -> bool:
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return True


def _current_temp_root() -> str:
    configured = os.environ.get("TEMP") or os.environ.get("TMP")
    if not configured:
        raise RuntimeError("The current user's temporary folder is unavailable.")
    root = os.path.abspath(configured)
    if not os.path.isdir(root) or _is_reparse(root):
        raise RuntimeError("The current user's temporary folder is unavailable or redirected.")
    return root


def scan_user_temp(days_old: int = 7) -> ScanResult:
    root = _current_temp_root()

    cutoff = time() - max(1, int(days_old)) * 24 * 60 * 60
    found: list[tuple[str, int]] = []
    truncated = False
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not _is_reparse(os.path.join(current, name))
        ]
        for name in files:
            path = os.path.abspath(os.path.join(current, name))
            try:
                info = os.stat(path, follow_symlinks=False)
                if info.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                    continue
                found.append((path, info.st_size))
                if len(found) >= MAX_PREVIEW_FILES:
                    truncated = True
                    break
            except (AttributeError, OSError):
                continue
        if truncated:
            break

    found.sort(key=lambda item: item[1], reverse=True)
    return ScanResult(found, truncated, root)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _safe_selected_paths(paths: list[str], scanned_root: str, days_old: int) -> list[str]:
    root = os.path.normcase(os.path.abspath(scanned_root))
    expected_root = os.path.normcase(_current_temp_root())
    if not root or root != expected_root:
        raise ValueError("The scanned folder is no longer the current temporary folder.")
    cutoff = time() - max(1, int(days_old)) * 24 * 60 * 60
    safe: list[str] = []
    for value in paths:
        path = os.path.normcase(os.path.abspath(value))
        try:
            if os.path.commonpath((root, path)) != root:
                continue
        except ValueError:
            continue
        if not os.path.isfile(path) or _is_reparse(path):
            continue
        try:
            if os.stat(path, follow_symlinks=False).st_mtime > cutoff:
                continue
        except OSError:
            continue
        parent = os.path.dirname(path)
        safe_tree = True
        while True:
            if _is_reparse(parent):
                safe_tree = False
                break
            if os.path.normcase(os.path.abspath(parent)) == root:
                break
            next_parent = os.path.dirname(parent)
            if next_parent == parent:
                safe_tree = False
                break
            parent = next_parent
        if not safe_tree:
            continue
        safe.append(path)
    return safe


def recycle_files(paths: list[str], scanned_root: str, days_old: int = 7) -> int:
    safe_paths = _safe_selected_paths(paths, scanned_root, days_old)
    if not safe_paths:
        return 0

    # SHFileOperation requires fully qualified paths and a double-NUL list.
    source = ctypes.create_unicode_buffer("\0".join(safe_paths) + "\0\0")
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = ctypes.cast(source, wintypes.LPCWSTR)
    operation.pTo = None
    # Let Windows ask again if a selected file cannot be placed in the Recycle Bin.
    operation.fFlags = 0x0004 | 0x0040 | 0x0400  # quiet, undo, no error UI
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(result, "Some files could not be moved to the Recycle Bin.")
    return len(safe_paths)
