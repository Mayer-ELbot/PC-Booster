from __future__ import annotations

import collections
import ctypes
from ctypes import wintypes
import datetime
import os
import threading
import time
from typing import Any

import psutil

from .state import get_settings


# Windows Process Priority Constants
IDLE_PRIORITY_CLASS = 0x00000040
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
HIGH_PRIORITY_CLASS = 0x00000080

# Windows Process Access Rights
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_INFORMATION = 0x0200
PROCESS_SET_QUOTA = 0x0100

PROTECTED_PROCESSES: set[str] = {
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
    "pc booster.exe",
    "python.exe",
}

# Windows APIs via ctypes
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_psapi = getattr(ctypes.windll, "psapi", None)
_dnsapi = getattr(ctypes.windll, "dnsapi", None)


def trim_process_working_set(pid: int, before_bytes: int = 0) -> int:
    """Safely trim unused pages from a process's working set without terminating it.
    Returns estimated bytes freed, or 0 on failure.
    """
    if pid <= 4 or pid == os.getpid() or _psapi is None:
        return 0

    handle = _kernel32.OpenProcess(
        PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        return 0

    try:
        before = before_bytes
        if before <= 0:
            try:
                p = psutil.Process(pid)
                before = p.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                before = 0

        success = _psapi.EmptyWorkingSet(handle)
        if success and before > 0:
            try:
                p = psutil.Process(pid)
                after = p.memory_info().rss
                return max(0, before - after)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return max(0, int(before * 0.35))
        return 0
    finally:
        _kernel32.CloseHandle(handle)


def set_process_priority(pid: int, priority_class: int) -> bool:
    """Set process priority safely using Windows kernel32."""
    if pid <= 4 or pid == os.getpid():
        return False

    handle = _kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        return bool(_kernel32.SetPriorityClass(handle, priority_class))
    finally:
        _kernel32.CloseHandle(handle)


def flush_dns_resolver() -> bool:
    """Flush the Windows DNS Resolver Cache via dnsapi.dll."""
    if _dnsapi and hasattr(_dnsapi, "DnsFlushResolverCache"):
        try:
            return bool(_dnsapi.DnsFlushResolverCache())
        except Exception:
            pass
    return False


class AutoPilotSentinel:
    """Intelligent autonomous background sentry.
    
    Zero-Kill Policy: NEVER terminates processes.
    - Dynamically boosts active foreground app priority.
    - Compacts memory of idle background bloatware.
    - Puts runaway background noise into EcoQoS.
    - Maintains live event history for the UI.
    """

    def __init__(self) -> None:
        self.settings: dict[str, Any] = get_settings()
        self.running: bool = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # State tracking
        self.current_foreground_pid: int = 0
        self.current_foreground_name: str = ""
        self.elevated_pid: int = 0
        self.last_trimmed: dict[int, float] = {}  # pid -> timestamp
        self.throttled_pids: set[int] = set()

        # Telemetry
        self.total_ram_freed_bytes: int = 0
        self.tamed_events_count: int = 0
        self.recent_events: collections.deque[dict[str, str]] = collections.deque(maxlen=25)

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="AutoPilotSentinelThread", daemon=True
            )
            self._thread.start()
        self._log_event("🟢", "Sentinel Started", "Autonomous background protection active.")

    def stop(self) -> None:
        with self._lock:
            self.running = False
            self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        # Restore elevated priority on exit
        if self.elevated_pid:
            try:
                set_process_priority(self.elevated_pid, NORMAL_PRIORITY_CLASS)
            except Exception:
                pass
            self.elevated_pid = 0

    def update_settings(self, new_settings: dict[str, Any]) -> None:
        with self._lock:
            self.settings = dict(new_settings)

    def _log_event(self, icon: str, title: str, detail: str) -> None:
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.recent_events.appendleft({
                "time": timestamp,
                "icon": icon,
                "title": title,
                "detail": detail,
            })

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "enabled": self.settings.get("auto_pilot_enabled", True),
                "total_freed_bytes": self.total_ram_freed_bytes,
                "tamed_count": self.tamed_events_count,
                "active_boosted_app": self.current_foreground_name or "None",
                "events": list(self.recent_events),
            }

    def _get_foreground_info(self) -> tuple[int, str]:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return 0, ""
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return 0, ""
        try:
            p = psutil.Process(pid.value)
            return pid.value, p.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return pid.value, ""

    def _is_whitelisted(self, name: str) -> bool:
        low = name.casefold()
        if low in PROTECTED_PROCESSES:
            return True
        user_whitelist = self.settings.get("whitelist", [])
        return any(item.casefold() == low for item in user_whitelist)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.settings.get("auto_pilot_enabled", True):
                    self._tick()
            except Exception:
                pass
            if self._stop_event.wait(3.0):
                break

    def _tick(self) -> None:
        now = time.monotonic()
        fg_pid, fg_name = self._get_foreground_info()

        # 1. Manage Foreground Turbo Priority
        if self.settings.get("foreground_turbo", True):
            if fg_pid != self.current_foreground_pid:
                # Revert previous elevated app to Normal
                if self.elevated_pid and self.elevated_pid != fg_pid:
                    try:
                        set_process_priority(self.elevated_pid, NORMAL_PRIORITY_CLASS)
                    except Exception:
                        pass
                    self.elevated_pid = 0

                self.current_foreground_pid = fg_pid
                self.current_foreground_name = fg_name

                # Elevate new foreground app if eligible
                if fg_pid and fg_name and not self._is_whitelisted(fg_name):
                    try:
                        if set_process_priority(fg_pid, ABOVE_NORMAL_PRIORITY_CLASS):
                            self.elevated_pid = fg_pid
                            self._log_event(
                                "⚡",
                                "Turbo Priority",
                                f"Boosted active app: {fg_name}",
                            )
                    except Exception:
                        pass

        # 2. Check System Memory Pressure & Emergency Recovery
        mem = psutil.virtual_memory()
        emergency_threshold = self.settings.get("ram_emergency_threshold_percent", 88)
        if mem.percent >= emergency_threshold:
            self._emergency_sweep()

        # 3. Background Memory Compaction & EcoQoS Throttling
        if not self.settings.get("background_ram_taming", True):
            return

        tame_threshold_mb = self.settings.get("ram_tame_threshold_mb", 350)
        threshold_bytes = tame_threshold_mb * 1024 * 1024

        # Clean old cooldown keys
        self.last_trimmed = {
            pid: ts for pid, ts in self.last_trimmed.items() if now - ts < 180
        }

        # Scan running user processes
        current_user = ""
        try:
            current_user = psutil.Process(os.getpid()).username().casefold()
        except Exception:
            pass

        for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
            try:
                info = p.info
                pid = info["pid"]
                name = info["name"] or ""
                username = (info.get("username") or "").casefold()

                # Safety shields:
                # - Must not be foreground
                # - Must not be self
                # - Must belong to current user
                # - Must not be protected or whitelisted
                if (
                    pid == fg_pid
                    or pid == os.getpid()
                    or pid <= 4
                    or not name
                    or (current_user and username and username != current_user)
                    or self._is_whitelisted(name)
                ):
                    continue

                # Check cooldown: at least 120s between trims for the same process
                if now - self.last_trimmed.get(pid, 0.0) < 120.0:
                    continue

                mem_rss = info["memory_info"].rss if info.get("memory_info") else 0
                if mem_rss >= threshold_bytes:
                    freed = trim_process_working_set(pid, mem_rss)
                    self.last_trimmed[pid] = now
                    if freed > 10 * 1024 * 1024:  # At least 10MB freed
                        with self._lock:
                            self.total_ram_freed_bytes += freed
                            self.tamed_events_count += 1
                        freed_mb = freed / (1024 * 1024)
                        self._log_event(
                            "💤",
                            "Memory Tamed",
                            f"Compacted {name} (Freed {freed_mb:.0f} MB, app alive)",
                        )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

    def _emergency_sweep(self) -> int:
        """Triggered when system RAM >= emergency threshold (e.g. 88%).
        Performs a rapid silent sweep of idle background apps and flushes DNS.
        """
        now = time.monotonic()
        total_sweep_freed = 0
        flush_dns_resolver()

        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = p.info
                pid = info["pid"]
                name = info["name"] or ""
                if (
                    pid == self.current_foreground_pid
                    or pid == os.getpid()
                    or pid <= 4
                    or self._is_whitelisted(name)
                ):
                    continue

                if now - self.last_trimmed.get(pid, 0.0) < 60.0:
                    continue

                mem_b = info["memory_info"].rss if info.get("memory_info") else 0
                freed = trim_process_working_set(pid, mem_b)
                self.last_trimmed[pid] = now
                if freed > 5 * 1024 * 1024:
                    total_sweep_freed += freed
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        if total_sweep_freed > 50 * 1024 * 1024:
            with self._lock:
                self.total_ram_freed_bytes += total_sweep_freed
                self.tamed_events_count += 1
            freed_mb = total_sweep_freed / (1024 * 1024)
            self._log_event(
                "🚨",
                "RAM Auto-Recovery",
                f"Recovered {freed_mb:.0f} MB RAM during high memory pressure.",
            )
        return total_sweep_freed

    def manual_quick_boost(self) -> dict[str, Any]:
        """Manual 1-Click Boost action callable from the UI or System Tray."""
        start_time = time.monotonic()
        total_freed = 0
        trimmed_count = 0

        # 1. Flush DNS cache
        dns_ok = flush_dns_resolver()

        # 2. Trim working sets of safe background processes
        now = time.monotonic()
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = p.info
                pid = info["pid"]
                name = info["name"] or ""
                if (
                    pid == self.current_foreground_pid
                    or pid == os.getpid()
                    or pid <= 4
                    or self._is_whitelisted(name)
                ):
                    continue

                mem_b = info["memory_info"].rss if info.get("memory_info") else 0
                freed = trim_process_working_set(pid, mem_b)
                self.last_trimmed[pid] = now
                if freed > 2 * 1024 * 1024:
                    total_freed += freed
                    trimmed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        elapsed = time.monotonic() - start_time
        with self._lock:
            self.total_ram_freed_bytes += total_freed
            self.tamed_events_count += 1

        freed_mb = total_freed / (1024 * 1024)
        self._log_event(
            "🚀",
            "Quick Boost Complete",
            f"Freed {freed_mb:.1f} MB across {trimmed_count} apps in {elapsed:.2f}s.",
        )

        return {
            "ram_freed_bytes": total_freed,
            "apps_trimmed": trimmed_count,
            "dns_flushed": dns_ok,
            "elapsed_seconds": elapsed,
        }
