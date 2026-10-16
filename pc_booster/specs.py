from __future__ import annotations

import os
import platform
import socket
import time
from typing import Any
import winreg

import psutil


def get_cpu_info() -> dict[str, Any]:
    name = platform.processor() or "Unknown CPU"
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if val:
                name = str(val).strip()
    except Exception:
        pass

    physical_cores = psutil.cpu_count(logical=False) or 1
    logical_cores = psutil.cpu_count(logical=True) or physical_cores
    freq = psutil.cpu_freq()
    cur_ghz = (freq.current / 1000.0) if freq and freq.current else 0.0

    return {
        "name": name,
        "physical_cores": physical_cores,
        "logical_cores": logical_cores,
        "current_ghz": cur_ghz,
    }


def get_memory_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "total_bytes": vm.total,
        "used_bytes": vm.used,
        "free_bytes": vm.available,
        "percent": vm.percent,
    }


def get_gpu_info() -> list[str]:
    names: list[str] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(16):
                try:
                    sub = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, sub) as sk:
                        driver, _ = winreg.QueryValueEx(sk, "DriverDesc")
                        if driver and str(driver) not in names:
                            names.append(str(driver))
                except OSError:
                    continue
    except OSError:
        pass
    return names or ["Standard Display Adapter"]


def get_drives_info() -> list[dict[str, Any]]:
    drives = []
    try:
        partitions = psutil.disk_partitions(all=False)
        for part in partitions:
            if not part.mountpoint or "cdrom" in part.opts.lower():
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                drives.append({
                    "device": part.device,
                    "mount": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                })
            except (OSError, PermissionError):
                continue
    except Exception:
        pass
    return drives


def get_os_info() -> dict[str, str]:
    product = f"Windows {platform.release()}"
    build = platform.version()
    disp = ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as k:
            try:
                product, _ = winreg.QueryValueEx(k, "ProductName")
            except Exception:
                pass
            try:
                build, _ = winreg.QueryValueEx(k, "CurrentBuildNumber")
            except Exception:
                pass
            try:
                disp, _ = winreg.QueryValueEx(k, "DisplayVersion")
            except Exception:
                pass
    except Exception:
        pass

    uptime_sec = int(time.time() - psutil.boot_time())
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {mins}m"

    hostname = socket.gethostname()

    return {
        "product": str(product),
        "build": str(build),
        "version_tag": f"{disp} (Build {build})" if disp else f"Build {build}",
        "architecture": platform.machine(),
        "uptime": uptime_str,
        "hostname": hostname,
    }


def get_full_system_specs() -> dict[str, Any]:
    return {
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "gpus": get_gpu_info(),
        "drives": get_drives_info(),
        "os": get_os_info(),
    }
