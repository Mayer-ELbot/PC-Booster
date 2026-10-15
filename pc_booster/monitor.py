from __future__ import annotations

import csv
import ctypes
import io
import math
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from typing import Any

import psutil


def _nvidia_stats() -> dict[str, Any] | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            return None
        rows = list(csv.reader(io.StringIO(result.stdout)))
        parsed = []
        for row in rows:
            if len(row) < 4:
                continue
            try:
                parsed.append(
                    {
                        "usage": float(row[0].strip()),
                        "memory_used": float(row[1].strip()),
                        "memory_total": float(row[2].strip()),
                        "temperature": float(row[3].strip()),
                    }
                )
            except ValueError:
                continue
        if not parsed:
            return None
        total_memory = sum(item["memory_total"] for item in parsed)
        used_memory = sum(item["memory_used"] for item in parsed)
        return {
            "usage": min(100.0, sum(item["usage"] for item in parsed) / len(parsed)),
            "memory_used": used_memory,
            "memory_total": total_memory,
            "temperature": max(item["temperature"] for item in parsed),
            "source": "NVIDIA",
        }
    except (OSError, subprocess.SubprocessError):
        return None


class _PdhCounterInfo(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("cVersion", wintypes.DWORD),
        ("cStatus", wintypes.DWORD),
        ("lScale", wintypes.LONG),
        ("lDefaultScale", wintypes.LONG),
        ("dwUserData", ctypes.c_size_t),
        ("dwQueryUserData", ctypes.c_size_t),
        ("szFullPath", wintypes.LPWSTR),
    ]


class _PdhCounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _WindowsGpuCounters:
    """Read Windows' per-engine GPU counters without spawning a polling process."""

    PDH_MORE_DATA = 0x800007D2
    PDH_FMT_DOUBLE = 0x00000200

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh.dll")
        self._configure_api()
        self._query = ctypes.c_void_p()
        self._counters: list[tuple[str, ctypes.c_void_p]] = []
        self._last_refresh = 0.0
        self._open_query()

    def _configure_api(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self._pdh.PdhAddCounterW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pdh.PdhAddCounterW.restype = wintypes.DWORD
        self._pdh.PdhRemoveCounter.argtypes = [ctypes.c_void_p]
        self._pdh.PdhRemoveCounter.restype = wintypes.DWORD
        self._pdh.PdhGetCounterInfoW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOLEAN,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._pdh.PdhGetCounterInfoW.restype = wintypes.DWORD
        self._pdh.PdhExpandWildCardPathW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        self._pdh.PdhExpandWildCardPathW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhCounterValue),
        ]
        self._pdh.PdhGetFormattedCounterValue.restype = wintypes.DWORD

    def _localized_wildcard(self, counter: ctypes.c_void_p) -> str:
        size = wintypes.DWORD(0)
        status = self._pdh.PdhGetCounterInfoW(
            counter, False, ctypes.byref(size), None
        )
        if status != self.PDH_MORE_DATA or size.value < ctypes.sizeof(_PdhCounterInfo):
            raise OSError(status, "Could not resolve the GPU counter path.")
        buffer = ctypes.create_string_buffer(size.value)
        status = self._pdh.PdhGetCounterInfoW(
            counter, False, ctypes.byref(size), buffer
        )
        if status != 0:
            raise OSError(status, "Could not read the GPU counter path.")
        info = ctypes.cast(buffer, ctypes.POINTER(_PdhCounterInfo)).contents
        if not info.szFullPath:
            raise OSError("The GPU counter path was empty.")
        return info.szFullPath

    def _expand_paths(self, wildcard: str) -> list[str]:
        size = wintypes.DWORD(0)
        status = self._pdh.PdhExpandWildCardPathW(
            None, wildcard, None, ctypes.byref(size), 0
        )
        if status != self.PDH_MORE_DATA or size.value <= 1:
            return []
        buffer = ctypes.create_unicode_buffer(size.value)
        status = self._pdh.PdhExpandWildCardPathW(
            None, wildcard, buffer, ctypes.byref(size), 0
        )
        if status != 0:
            return []
        return [path for path in buffer[: size.value].split("\0") if path]

    def _open_query(self) -> None:
        self._close_query()
        query = ctypes.c_void_p()
        status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
        if status != 0:
            raise OSError(status, "Could not open a Windows performance query.")
        self._query = query
        english_counter = ctypes.c_void_p()
        try:
            status = self._pdh.PdhAddEnglishCounterW(
                query,
                r"\GPU Engine(*)\Utilization Percentage",
                0,
                ctypes.byref(english_counter),
            )
            if status != 0:
                raise OSError(status, "The Windows GPU engine counter is unavailable.")
            localized = self._localized_wildcard(english_counter)
            self._pdh.PdhRemoveCounter(english_counter)
            paths = self._expand_paths(localized)
            for path in paths:
                match = re.search(r"GPU Engine\(([^)]*)\)", path, re.IGNORECASE)
                if not match or not any(
                    kind in match.group(1).casefold()
                    for kind in ("engtype_3d", "engtype_compute", "engtype_video", "engtype_copy")
                ):
                    continue
                counter = ctypes.c_void_p()
                if self._pdh.PdhAddCounterW(query, path, 0, ctypes.byref(counter)) == 0:
                    self._counters.append((match.group(1), counter))
            if not self._counters:
                raise OSError("No 3D GPU engine counters were found.")
            self._pdh.PdhCollectQueryData(query)
            self._last_refresh = time.monotonic()
        except Exception:
            self._close_query()
            raise

    def _close_query(self) -> None:
        if getattr(self, "_query", None) and self._query.value:
            self._pdh.PdhCloseQuery(self._query)
        self._query = ctypes.c_void_p()
        self._counters = []

    def sample(self) -> float | None:
        if not self._query.value:
            try:
                self._open_query()
            except OSError:
                return None
        if time.monotonic() - self._last_refresh >= 30:
            try:
                self._open_query()
            except OSError:
                return None
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            self._close_query()
            return None

        by_engine: dict[str, float] = {}
        fallback_values: list[float] = []
        for instance, counter in self._counters:
            value = _PdhCounterValue()
            status = self._pdh.PdhGetFormattedCounterValue(
                counter, self.PDH_FMT_DOUBLE, None, ctypes.byref(value)
            )
            if status != 0 or value.CStatus not in (0, 1) or not math.isfinite(value.doubleValue):
                continue
            amount = max(0.0, value.doubleValue)
            fallback_values.append(amount)
            engine = re.search(
                r"^(.*_phys_\d+_eng_\d+)_engtype_3d",
                instance,
                re.IGNORECASE,
            )
            key = engine.group(1).casefold() if engine else instance.casefold()
            by_engine[key] = by_engine.get(key, 0.0) + amount
        if by_engine:
            return min(100.0, max(by_engine.values()))
        if fallback_values:
            return min(100.0, max(fallback_values))
        return None

    def close(self) -> None:
        self._close_query()


class SystemMonitor:
    def __init__(self) -> None:
        self._previous_disk = psutil.disk_io_counters()
        self._previous_time = time.monotonic()
        self._previous_cpu_times = psutil.cpu_times()
        self._process_cpu_samples: dict[int, tuple[float, float, float]] = {}
        self._last_gpu_time = 0.0
        self._gpu: dict[str, Any] | None = None
        try:
            self._gpu_counters: _WindowsGpuCounters | None = _WindowsGpuCounters()
        except Exception:
            self._gpu_counters = None

    def sample(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disk_io = psutil.disk_io_counters()
        now = time.monotonic()
        cpu_times = psutil.cpu_times()
        total_cpu_delta = max(0.0, sum(cpu_times) - sum(self._previous_cpu_times))
        idle_cpu_delta = max(0.0, cpu_times.idle - self._previous_cpu_times.idle)
        cpu = (
            min(100.0, max(0.0, (total_cpu_delta - idle_cpu_delta) / total_cpu_delta * 100))
            if total_cpu_delta > 0
            else 0.0
        )
        self._previous_cpu_times = cpu_times
        elapsed = max(now - self._previous_time, 0.1)
        read_rate = 0.0
        write_rate = 0.0
        if disk_io and self._previous_disk:
            read_rate = max(0, disk_io.read_bytes - self._previous_disk.read_bytes) / elapsed
            write_rate = max(0, disk_io.write_bytes - self._previous_disk.write_bytes) / elapsed
        self._previous_disk = disk_io
        self._previous_time = now

        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        try:
            disk = psutil.disk_usage(system_drive)
            disk_root = system_drive
        except OSError:
            partitions = psutil.disk_partitions(all=False)
            disk_root = partitions[0].mountpoint if partitions else system_drive
            try:
                disk = psutil.disk_usage(disk_root)
            except OSError:
                disk = None

        if now - self._last_gpu_time >= 5.0:
            try:
                usage = self._gpu_counters.sample() if self._gpu_counters else None
            except Exception:
                usage = None
            self._gpu = (
                {"usage": usage, "source": "Windows GPU Engine"}
                if usage is not None
                else _nvidia_stats()
            )
            self._last_gpu_time = now

        processes = []
        logical_cpus = max(psutil.cpu_count() or 1, 1)
        process_sample_time = time.monotonic()
        next_process_cpu_samples: dict[int, tuple[float, float, float]] = {}
        for process in psutil.process_iter(["pid", "name", "memory_info", "create_time"]):
            try:
                info = process.info
                name = info["name"] or "Unnamed process"
                if info["pid"] == 0 or name.casefold() == "system idle process":
                    continue
                memory_bytes = info["memory_info"].rss if info["memory_info"] else 0
                created = info["create_time"]
                times = process.cpu_times()
                cpu_seconds = times.user + times.system
                previous = self._process_cpu_samples.get(info["pid"])
                process_cpu = 0.0
                if previous and previous[0] == created:
                    elapsed = max(process_sample_time - previous[2], 0.1)
                    process_cpu = max(0.0, cpu_seconds - previous[1]) / elapsed / logical_cpus * 100
                next_process_cpu_samples[info["pid"]] = (
                    created,
                    cpu_seconds,
                    process_sample_time,
                )
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": name,
                        "memory": memory_bytes,
                        "cpu": min(100.0, process_cpu),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        self._process_cpu_samples = next_process_cpu_samples
        by_memory = sorted(processes, key=lambda item: item["memory"], reverse=True)[:6]
        by_cpu = sorted(processes, key=lambda item: item["cpu"], reverse=True)[:6]
        selected_ids = {item["pid"] for item in by_memory + by_cpu}
        top_processes = [item for item in processes if item["pid"] in selected_ids]
        top_processes.sort(key=lambda item: (item["cpu"], item["memory"]), reverse=True)

        return {
            "cpu": cpu,
            "memory_used": memory.total - memory.available,
            "memory_total": memory.total,
            "memory_available": memory.available,
            "memory_percent": max(0.0, min(100.0, (memory.total - memory.available) / memory.total * 100)),
            "disk": disk,
            "disk_root": disk_root,
            "disk_read_rate": read_rate,
            "disk_write_rate": write_rate,
            "gpu": self._gpu,
            "processes": top_processes,
        }

    def close(self) -> None:
        if self._gpu_counters:
            try:
                self._gpu_counters.close()
            except Exception:
                pass
