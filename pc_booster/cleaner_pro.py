from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import glob
import os
import shutil
from typing import Any


_shell32 = ctypes.windll.shell32


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


def get_recycle_bin_info() -> tuple[int, int]:
    """Returns (item_count, total_bytes) of the Recycle Bin."""
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    try:
        res = _shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if res == 0:
            return max(0, int(info.i64NumItems)), max(0, int(info.i64Size))
    except Exception:
        pass
    return 0, 0


def empty_recycle_bin() -> bool:
    """Safely empty the Recycle Bin with no confirmation prompt or sound."""
    # SHERB_NOCONFIRMATION = 0x00000001
    # SHERB_NOPROGRESSUI   = 0x00000002
    # SHERB_NOSOUND        = 0x00000004
    flags = 0x00000001 | 0x00000002 | 0x00000004
    try:
        res = _shell32.SHEmptyRecycleBinW(None, None, flags)
        return res == 0
    except Exception:
        return False


@dataclass
class CleanItem:
    path: str
    size: int


@dataclass
class CleanCategory:
    id: str
    name: str
    description: str
    is_recycle_bin: bool = False
    items: list[CleanItem] = field(default_factory=list)
    total_bytes: int = 0
    scanned: bool = False

    @property
    def count(self) -> int:
        return len(self.items)


def _scan_directory_files(directory: str, max_files: int = 1500) -> list[CleanItem]:
    items: list[CleanItem] = []
    if not os.path.isdir(directory):
        return items

    try:
        for root, _, files in os.walk(directory, topdown=True, followlinks=False):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                try:
                    # Ignore symlinks/reparse points
                    st = os.stat(full_path, follow_symlinks=False)
                    if hasattr(st, "st_file_attributes") and (st.st_file_attributes & 0x400):
                        continue
                    items.append(CleanItem(path=full_path, size=st.st_size))
                    if len(items) >= max_files:
                        return items
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        pass
    return items


def scan_clean_categories() -> list[CleanCategory]:
    """Scan all safe cleaning targets across the system."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    temp_dir = os.environ.get("TEMP", "")
    win_dir = os.environ.get("SystemRoot", r"C:\Windows")
    system_temp = os.path.join(win_dir, "Temp")

    categories: list[CleanCategory] = []

    # 1. User Temp Files
    user_temp_cat = CleanCategory(
        id="user_temp",
        name="User Temporary Files",
        description="Cached application temporary files in your user profile (%TEMP%).",
    )
    if temp_dir and os.path.isdir(temp_dir):
        user_temp_cat.items = _scan_directory_files(temp_dir)
        user_temp_cat.total_bytes = sum(i.size for i in user_temp_cat.items)
        user_temp_cat.scanned = True
    categories.append(user_temp_cat)

    # 2. Windows System Temp
    sys_temp_cat = CleanCategory(
        id="system_temp",
        name="Windows System Temp",
        description="Temporary operating system files in C:\\Windows\\Temp.",
    )
    if os.path.isdir(system_temp):
        sys_temp_cat.items = _scan_directory_files(system_temp, max_files=800)
        sys_temp_cat.total_bytes = sum(i.size for i in sys_temp_cat.items)
        sys_temp_cat.scanned = True
    categories.append(sys_temp_cat)

    # 3. Google Chrome Cache
    chrome_cache = os.path.join(local_app_data, r"Google\Chrome\User Data\Default\Cache")
    if os.path.isdir(chrome_cache):
        chrome_cat = CleanCategory(
            id="chrome_cache",
            name="Google Chrome Web Cache",
            description="Cached web pages, images, and script assets from Google Chrome.",
        )
        chrome_cat.items = _scan_directory_files(chrome_cache)
        chrome_cat.total_bytes = sum(i.size for i in chrome_cat.items)
        chrome_cat.scanned = True
        categories.append(chrome_cat)

    # 4. Microsoft Edge Cache
    edge_cache = os.path.join(local_app_data, r"Microsoft\Edge\User Data\Default\Cache")
    if os.path.isdir(edge_cache):
        edge_cat = CleanCategory(
            id="edge_cache",
            name="Microsoft Edge Web Cache",
            description="Web browser cache and downloaded temporary media from Edge.",
        )
        edge_cat.items = _scan_directory_files(edge_cache)
        edge_cat.total_bytes = sum(i.size for i in edge_cat.items)
        edge_cat.scanned = True
        categories.append(edge_cat)

    # 5. Windows Crash Dumps & Error Reports
    crash_dir = os.path.join(local_app_data, "CrashDumps")
    if os.path.isdir(crash_dir):
        crash_cat = CleanCategory(
            id="crash_dumps",
            name="Windows Crash Dumps",
            description="Old memory crash dumps (.dmp) and software error traces.",
        )
        crash_cat.items = _scan_directory_files(crash_dir)
        crash_cat.total_bytes = sum(i.size for i in crash_cat.items)
        crash_cat.scanned = True
        categories.append(crash_cat)

    # 6. Recycle Bin
    rb_items, rb_bytes = get_recycle_bin_info()
    rb_cat = CleanCategory(
        id="recycle_bin",
        name="Recycle Bin",
        description="Files and folders currently sitting in your Windows Recycle Bin.",
        is_recycle_bin=True,
        total_bytes=rb_bytes,
        scanned=True,
    )
    # Fake placeholder items for count display if items > 0
    if rb_items > 0:
        rb_cat.items = [CleanItem("RecycleBinItem", rb_bytes // max(rb_items, 1))] * rb_items
    categories.append(rb_cat)

    return categories


def clean_categories(categories: list[CleanCategory], selected_ids: set[str]) -> dict[str, Any]:
    """Clean only the user-selected categories."""
    total_freed_bytes = 0
    files_deleted = 0
    errors: list[str] = []

    for cat in categories:
        if cat.id not in selected_ids:
            continue

        if cat.is_recycle_bin:
            rb_items, rb_bytes = get_recycle_bin_info()
            if empty_recycle_bin():
                total_freed_bytes += rb_bytes
                files_deleted += rb_items
            else:
                errors.append("Could not empty Recycle Bin.")
            continue

        for item in cat.items:
            try:
                if os.path.isfile(item.path):
                    os.remove(item.path)
                    total_freed_bytes += item.size
                    files_deleted += 1
            except (OSError, PermissionError):
                # File is currently locked or in use by another app, skip safely
                continue

    return {
        "freed_bytes": total_freed_bytes,
        "files_deleted": files_deleted,
        "errors": errors,
    }
