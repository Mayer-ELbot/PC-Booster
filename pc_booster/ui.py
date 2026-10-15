from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import __version__
from . import autostart
from .cleanup import ScanResult, recycle_files, scan_user_temp
from .monitor import SystemMonitor
from . import ram_manager, startup, tweaks
from .state import read_json, write_json


BG = "#0b1215"
SIDEBAR = "#101a1d"
SURFACE = "#142125"
SURFACE_ALT = "#19292d"
BORDER = "#263a3e"
TEXT = "#e8f2ef"
MUTED = "#9aafaa"
ACCENT = "#50d8b4"
ACCENT_DARK = "#163b34"
HERO_BG = "#0e191e"
WARN = "#f0b45d"
WARN_BG = "#3a2c18"
FONT = "Segoe UI"


def _asset_path(name: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return bundle_root / "assets" / name


def _format_bytes(value: int | float) -> str:
    amount = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def _short(value: str, limit: int = 125) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


class PCBooster(tk.Tk):
    def __init__(self, start_in_background: bool = False) -> None:
        super().__init__()
        if start_in_background:
            self.withdraw()
        self.title("PC Booster")
        self.geometry("1180x800")
        self.minsize(1080, 720)
        self.configure(bg=BG)
        self.option_add("*Font", (FONT, 10))
        try:
            self.iconbitmap(str(_asset_path("pc_booster.ico")))
        except tk.TclError:
            pass
        try:
            self.hero_art = tk.PhotoImage(file=str(_asset_path("dashboard_hero_small.png")))
        except tk.TclError:
            self.hero_art = None
        self.style = ttk.Style(self)
        self._configure_style()

        prefs = read_json("settings.json", {})
        if not isinstance(prefs, dict):
            prefs = {}
        alert_setting = prefs.get("auto_memory_alert", True)
        self.auto_memory_alert = alert_setting if isinstance(alert_setting, bool) else True
        tray_setting = prefs.get("close_to_tray", True)
        self.close_to_tray = tray_setting if isinstance(tray_setting, bool) else True
        self.start_in_background = start_in_background
        self.tray_icon: Any | None = None
        self.monitor = SystemMonitor()
        self.last_snapshot: dict[str, Any] | None = None
        self.low_memory_samples = 0
        self.memory_pressure = False
        self.busy = False
        self.monitor_busy = False
        self.closing = False
        self.current_page = "home"
        self.pending: queue.Queue[tuple[Callable[..., None], Any, Exception | None]] = queue.Queue()
        self.monitor_pending: queue.Queue[tuple[dict[str, Any] | None, Exception | None]] = queue.Queue()
        self.scan_result: ScanResult | None = None
        self.scan_days: int | None = None
        self.scan_rows: dict[str, tuple[str, int]] = {}
        self.ram_rows: dict[str, dict[str, Any]] = {}
        self.startup_active_rows: dict[str, str] = {}
        self.startup_disabled_rows: dict[str, str] = {}
        self.metric_widgets: dict[str, dict[str, Any]] = {}
        self.tweak_vars: dict[str, tk.BooleanVar] = {}
        self.tweak_status_vars: dict[str, tk.StringVar] = {}
        self.tweak_status_labels: dict[str, tk.Label] = {}

        self._build_shell()
        self.show_page("home")
        self.after(120, self._poll_pending)
        self.after(700, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.bind("<Unmap>", self._on_unmap)
        if self.start_in_background:
            self.after(0, self._hide_to_tray)

    def _configure_style(self) -> None:
        self.style.theme_use("clam")
        self.style.configure(
            "Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
            font=(FONT, 9),
        )
        self.style.configure(
            "Treeview.Heading",
            background=SURFACE_ALT,
            foreground=MUTED,
            relief="flat",
            font=(FONT, 9, "bold"),
        )
        self.style.map(
            "Treeview",
            background=[("selected", ACCENT_DARK)],
            foreground=[("selected", TEXT)],
        )
        self.style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor=SURFACE_ALT,
            background=ACCENT,
            bordercolor=SURFACE,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=7,
        )
        self.style.configure(
            "Amber.Horizontal.TProgressbar",
            troughcolor=SURFACE_ALT,
            background=WARN,
            bordercolor=SURFACE,
            lightcolor=WARN,
            darkcolor=WARN,
            thickness=7,
        )
        self.style.configure(
            "Dark.TCombobox",
            fieldbackground=SURFACE_ALT,
            background=SURFACE_ALT,
            foreground=TEXT,
            arrowcolor=ACCENT,
            bordercolor=BORDER,
        )
        self.style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", SURFACE_ALT)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", SURFACE_ALT)],
            selectforeground=[("readonly", TEXT)],
        )
        self.style.configure("Dark.TCheckbutton", background=SURFACE, foreground=TEXT)
        self.style.map("Dark.TCheckbutton", background=[("active", SURFACE)])
        self.style.configure(
            "Dark.Vertical.TScrollbar",
            background=SURFACE_ALT,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            width=10,
        )
        self.style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", ACCENT_DARK)],
        )

    def _build_shell(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(shell, bg=SIDEBAR, width=222, highlightthickness=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        main = tk.Frame(shell, bg=BG)
        main.pack(side="right", fill="both", expand=True)
        header = tk.Frame(main, bg=BG, padx=28, pady=10)
        header.pack(fill="x")
        header_left = tk.Frame(header, bg=BG)
        header_left.pack(side="left", anchor="w")
        tk.Label(
            header_left,
            text="PC BOOSTER",
            bg=BG,
            fg=ACCENT,
            font=(FONT, 9, "bold"),
        ).pack(anchor="w")
        self.page_title = tk.Label(
            header_left,
            text="",
            bg=BG,
            fg=TEXT,
            font=(FONT, 19, "bold"),
        )
        self.page_title.pack(anchor="w", pady=(3, 0))

        live = tk.Frame(header, bg=ACCENT_DARK, padx=12, pady=7)
        live.pack(side="right", anchor="center")
        tk.Label(live, text="●", bg=ACCENT_DARK, fg=ACCENT, font=(FONT, 9)).pack(
            side="left", padx=(7, 0)
        )
        tk.Label(
            live, text="Local monitoring", bg=ACCENT_DARK, fg=TEXT, font=(FONT, 9, "bold")
        ).pack(side="left")

        footer = tk.Frame(main, bg=BG, padx=28, pady=12)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, 9))
        self.status_var = tk.StringVar(value="Monitoring runs locally on this PC.")
        self.status_label = tk.Label(
            footer, textvariable=self.status_var, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w"
        )
        self.status_label.pack(fill="x")

        body = tk.Frame(main, bg=BG)
        body.pack(fill="both", expand=True)
        self.page_canvas = tk.Canvas(body, bg=BG, bd=0, highlightthickness=0)
        self.page_scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=self.page_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.page_canvas.configure(yscrollcommand=self.page_scrollbar.set)
        self.page_canvas.pack(side="left", fill="both", expand=True)
        self.page_scrollbar.pack(side="right", fill="y")
        self.page_body = tk.Frame(self.page_canvas, bg=BG, padx=28, pady=4)
        self.page_body_window = self.page_canvas.create_window(
            (0, 0), window=self.page_body, anchor="nw"
        )
        self.page_body.bind("<Configure>", self._sync_scroll_region)
        self.page_canvas.bind("<Configure>", self._resize_page_body)

    def _sync_scroll_region(self, _event: Any = None) -> None:
        self.page_canvas.configure(scrollregion=self.page_canvas.bbox("all"))

    def _resize_page_body(self, event: tk.Event) -> None:
        self.page_canvas.itemconfigure(self.page_body_window, width=event.width)

    def _build_sidebar(self) -> None:
        brand = tk.Frame(self.sidebar, bg=SIDEBAR, padx=20, pady=22)
        brand.pack(fill="x")
        mark = tk.Canvas(brand, width=34, height=34, bg=SIDEBAR, highlightthickness=0)
        mark.create_oval(2, 2, 32, 32, outline=ACCENT, width=2)
        mark.create_arc(8, 8, 26, 26, start=45, extent=290, outline=ACCENT, width=3, style="arc")
        mark.pack(side="left", padx=(0, 9))
        title = tk.Frame(brand, bg=SIDEBAR)
        title.pack(side="left")
        tk.Label(title, text="PC Booster", bg=SIDEBAR, fg=TEXT, font=(FONT, 12, "bold")).pack(
            anchor="w"
        )
        tk.Label(title, text="A safe, lightweight toolkit", bg=SIDEBAR, fg=MUTED, font=(FONT, 8)).pack(
            anchor="w", pady=(2, 0)
        )

        tk.Frame(self.sidebar, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(0, 18))
        self.nav_buttons: dict[str, tk.Button] = {}
        links = [
            ("home", "Overview"),
            ("memory", "Memory Manager"),
            ("startup", "Startup Apps"),
            ("cleanup", "Storage Cleanup"),
            ("tweaks", "Windows Tweaks"),
        ]
        for key, label in links:
            button = tk.Button(
                self.sidebar,
                text=label,
                anchor="w",
                padx=18,
                pady=13,
                bg=SIDEBAR,
                fg=MUTED,
                activebackground=SURFACE_ALT,
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                font=(FONT, 10),
                cursor="hand2",
                command=lambda page=key: self.show_page(page),
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button

        footer = tk.Frame(self.sidebar, bg=SIDEBAR, padx=18, pady=18)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, 12))
        tk.Label(
            footer,
            text=f"Version {__version__}",
            bg=SIDEBAR,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            footer,
            text="Your data stays on this PC",
            bg=SIDEBAR,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

    def _button(
        self,
        parent: tk.Misc,
        label: str,
        command: Callable[[], None],
        primary: bool = False,
        compact: bool = False,
    ) -> tk.Button:
        background = ACCENT_DARK if primary else SURFACE_ALT
        foreground = ACCENT if primary else TEXT
        button = tk.Button(
            parent,
            text=label,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=ACCENT if primary else BORDER,
            activeforeground=BG if primary else TEXT,
            relief="flat",
            bd=0,
            padx=14 if compact else 18,
            pady=8 if compact else 10,
            font=(FONT, 9 if compact else 10, "bold"),
            cursor="hand2",
            takefocus=True,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        button.bind("<FocusIn>", lambda event: event.widget.configure(highlightbackground=ACCENT))
        button.bind("<FocusOut>", lambda event: event.widget.configure(highlightbackground=BORDER))
        return button

    def _section_heading(self, parent: tk.Misc, title: str, description: str = "") -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", pady=(0, 10))
        tk.Label(frame, text=title, bg=BG, fg=TEXT, font=(FONT, 15, "bold"), anchor="w").pack(
            fill="x"
        )
        if description:
            tk.Label(
                frame, text=description, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w"
            ).pack(fill="x", pady=(5, 0))
        return frame

    def _card(self, parent: tk.Misc, padx: int = 17, pady: int = 15) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            padx=padx,
            pady=pady,
            highlightthickness=1,
            highlightbackground=BORDER,
        )

    def show_page(self, page: str) -> None:
        self.current_page = page
        self.page_canvas.yview_moveto(0)
        for child in self.page_body.winfo_children():
            child.destroy()
        titles = {
            "home": "Overview",
            "memory": "Memory Manager",
            "startup": "Startup Apps",
            "cleanup": "Storage Cleanup",
            "tweaks": "Windows Tweaks",
        }
        self.page_title.configure(text=titles.get(page, "PC Booster"))
        for key, button in self.nav_buttons.items():
            active = key == page
            button.configure(
                bg=ACCENT_DARK if active else SIDEBAR,
                fg=ACCENT if active else MUTED,
                font=(FONT, 10, "bold" if active else "normal"),
            )
        if page == "home":
            self._render_home()
        elif page == "memory":
            self._render_memory()
        elif page == "startup":
            self._render_startup()
        elif page == "cleanup":
            self._render_cleanup()
        elif page == "tweaks":
            self._render_tweaks()

    def _render_home(self) -> None:
        self._section_heading(
            self.page_body,
            "System Overview",
            "Live CPU, memory, storage, and GPU readings while the app is open. No apps are closed automatically.",
        )

        monitor_row = tk.Frame(self.page_body, bg=BG)
        monitor_row.pack(fill="x", pady=(0, 6))
        self.memory_banner = tk.Frame(monitor_row, bg=HERO_BG, height=100)
        self.memory_banner.pack(side="left", fill="x", expand=True)
        self.memory_banner.pack_propagate(False)
        memory_copy = tk.Frame(self.memory_banner, bg=HERO_BG, padx=14, pady=8)
        memory_copy.pack(side="left", fill="both", expand=True)
        self.memory_copy = memory_copy
        self.memory_banner_title = tk.Label(
            memory_copy,
            text="Monitoring is active",
            bg=HERO_BG,
            fg=ACCENT,
            font=(FONT, 10, "bold"),
            anchor="w",
        )
        self.memory_banner_title.pack(fill="x")
        self.memory_banner_text = tk.Label(
            memory_copy,
            text="If available memory stays low, we will show the apps using the most memory without closing them.",
            bg=HERO_BG,
            fg=TEXT,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=380,
        )
        self.memory_banner_text.pack(fill="x", pady=(5, 0))
        self._button(
            memory_copy,
            "Open Memory Manager",
            lambda: self.show_page("memory"),
            compact=True,
        ).pack(anchor="w", pady=(4, 0))
        if self.hero_art:
            self.hero_art_label = tk.Label(
                self.memory_banner, image=self.hero_art, bg=HERO_BG, bd=0
            )
            self.hero_art_label.pack(side="right", padx=(0, 5))
        else:
            self.hero_art_label = None

        alert = tk.Frame(monitor_row, bg=SURFACE, padx=11, pady=8)
        alert.pack(side="right", padx=(10, 0))
        self.alert_var = tk.BooleanVar(value=self.auto_memory_alert)
        check = ttk.Checkbutton(
            alert,
            text="Memory alert",
            variable=self.alert_var,
            style="Dark.TCheckbutton",
            command=self._toggle_memory_alert,
        )
        check.pack()

        cards = tk.Frame(self.page_body, bg=BG)
        cards.pack(fill="x", pady=(0, 6))
        cards.grid_columnconfigure(0, weight=1, uniform="metric")
        cards.grid_columnconfigure(1, weight=1, uniform="metric")
        specifications = [
            ("cpu", "CPU", "Current usage"),
            ("memory", "Memory", "Available"),
            ("disk", "System drive", "Used space"),
            ("gpu", "Graphics", "GPU activity"),
        ]
        for index, (key, title, hint) in enumerate(specifications):
            card = self._metric_card(cards, key, title, hint)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=3)

        process_card = self._card(self.page_body, padx=12, pady=8)
        process_card.pack(fill="both", expand=True)
        process_header = tk.Frame(process_card, bg=SURFACE)
        process_header.pack(fill="x", pady=(0, 5))
        tk.Label(
            process_header,
            text="Top CPU and memory apps",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
            anchor="w",
        ).pack(side="left")
        self._button(
            process_header, "Task Manager", self._open_task_manager, compact=True
        ).pack(side="right")
        tk.Label(
            process_header,
            text="Read only",
            bg=SURFACE,
            fg=ACCENT,
            font=(FONT, 8, "bold"),
            anchor="e",
        ).pack(side="right")
        columns = ("name", "cpu", "memory")
        self.process_tree = ttk.Treeview(
            process_card, columns=columns, show="headings", height=4, selectmode="none"
        )
        self.process_tree.heading("name", text="App")
        self.process_tree.heading("cpu", text="CPU")
        self.process_tree.heading("memory", text="RAM")
        self.process_tree.column("name", width=480, anchor="w")
        self.process_tree.column("cpu", width=90, anchor="center", stretch=False)
        self.process_tree.column("memory", width=110, anchor="center", stretch=False)
        process_scroll = ttk.Scrollbar(
            process_card, orient="vertical", command=self.process_tree.yview
        )
        self.process_tree.configure(yscrollcommand=process_scroll.set)
        self.process_tree.pack(side="left", fill="both", expand=True)
        process_scroll.pack(side="right", fill="y")
        if self.last_snapshot:
            self._update_home(self.last_snapshot)

    def _metric_card(self, parent: tk.Misc, key: str, title: str, hint: str) -> tk.Frame:
        card = self._card(parent, padx=14, pady=6)
        heading = tk.Frame(card, bg=SURFACE)
        heading.pack(fill="x")
        tk.Label(heading, text=title, bg=SURFACE, fg=TEXT, font=(FONT, 10, "bold")).pack(
            side="left"
        )
        tk.Label(heading, text=hint, bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(
            side="right"
        )
        value = tk.Label(card, text="--", bg=SURFACE, fg=TEXT, font=(FONT, 19, "bold"), anchor="w")
        value.pack(fill="x", pady=(5, 2))
        progress = ttk.Progressbar(
            card,
            mode="determinate",
            maximum=100,
            style="Green.Horizontal.TProgressbar",
        )
        progress.pack(fill="x", pady=(0, 4))
        detail = tk.Label(card, text="Reading...", bg=SURFACE, fg=MUTED, font=(FONT, 8), anchor="w")
        detail.pack(fill="x")
        self.metric_widgets[key] = {"value": value, "progress": progress, "detail": detail}
        return card

    def _update_home(self, data: dict[str, Any]) -> None:
        if self.current_page != "home" or not self.metric_widgets:
            return
        cpu = self.metric_widgets.get("cpu")
        if cpu:
            cpu["value"].configure(text=f"{data['cpu']:.0f}%")
            cpu["progress"].configure(value=data["cpu"])
            cpu["detail"].configure(text=f"{data['cpu']:.1f}% of CPU capacity")

        memory = self.metric_widgets.get("memory")
        if memory:
            percentage = data["memory_percent"]
            memory["value"].configure(text=f"{percentage:.0f}% used")
            memory["progress"].configure(
                value=percentage,
                style="Amber.Horizontal.TProgressbar" if percentage >= 90 else "Green.Horizontal.TProgressbar",
            )
            memory["detail"].configure(
                text=f"Free {_format_bytes(data['memory_available'])} of {_format_bytes(data['memory_total'])}"
            )

        disk_metric = self.metric_widgets.get("disk")
        disk = data.get("disk")
        if disk_metric:
            if disk:
                disk_metric["value"].configure(text=f"{disk.percent:.0f}% used")
                disk_metric["progress"].configure(
                    value=disk.percent,
                    style="Amber.Horizontal.TProgressbar" if disk.free < 10 * 1024**3 else "Green.Horizontal.TProgressbar",
                )
                disk_metric["detail"].configure(
                    text=(
                        f"{data['disk_root']} • Free {_format_bytes(disk.free)}"
                        f" • Read {_format_bytes(data['disk_read_rate'])}/s"
                        f" • Write {_format_bytes(data['disk_write_rate'])}/s"
                    )
                )
            else:
                disk_metric["value"].configure(text="Unavailable")
                disk_metric["detail"].configure(text="Could not read the system drive")

        gpu_metric = self.metric_widgets.get("gpu")
        gpu = data.get("gpu")
        if gpu_metric:
            if gpu:
                gpu_metric["value"].configure(text=f"{gpu['usage']:.0f}%")
                gpu_metric["progress"].configure(value=gpu["usage"])
                if gpu.get("source") == "NVIDIA":
                    detail = (
                        f"NVIDIA • Memory {_format_bytes(gpu['memory_used'] * 1024**2)}"
                        f" / {_format_bytes(gpu['memory_total'] * 1024**2)}"
                        f" • {gpu['temperature']:.0f}°C"
                    )
                else:
                    detail = "Windows GPU engine activity"
                gpu_metric["detail"].configure(text=detail)
            else:
                gpu_metric["value"].configure(text="Unavailable")
                gpu_metric["progress"].configure(value=0)
                gpu_metric["detail"].configure(text="GPU data unavailable. Check the graphics driver.")

        if hasattr(self, "process_tree") and self.process_tree.winfo_exists():
            self.process_tree.delete(*self.process_tree.get_children())
            for process in data["processes"][:10]:
                self.process_tree.insert(
                    "",
                    "end",
                    values=(
                        process["name"],
                        f"{process['cpu']:.1f}%",
                        _format_bytes(process["memory"]),
                    ),
                )

        if self.auto_memory_alert and self.memory_pressure:
            self.memory_banner.configure(bg=WARN_BG)
            self.memory_copy.configure(bg=WARN_BG)
            if self.hero_art_label:
                self.hero_art_label.configure(bg=HERO_BG)
            self.memory_banner_title.configure(
                text="Available memory has stayed low", bg=WARN_BG, fg=WARN
            )
            self.memory_banner_text.configure(
                text="Review the highest-usage apps. We explain the pressure; we do not force-close apps or purge memory.",
                bg=WARN_BG,
            )
        else:
            self.memory_banner.configure(bg=HERO_BG)
            self.memory_copy.configure(bg=HERO_BG)
            if self.hero_art_label:
                self.hero_art_label.configure(bg=HERO_BG)
            self.memory_banner_title.configure(
                text="Monitoring is active", bg=HERO_BG, fg=ACCENT
            )
            self.memory_banner_text.configure(
                text="If available memory stays low, we will show the apps using the most memory without closing them.",
                bg=HERO_BG,
            )

    def _toggle_memory_alert(self) -> None:
        previous = self.auto_memory_alert
        self.auto_memory_alert = bool(self.alert_var.get())
        try:
            write_json("settings.json", {"auto_memory_alert": self.auto_memory_alert})
        except OSError as error:
            self.auto_memory_alert = previous
            self.alert_var.set(previous)
            self.show_error(f"Could not save the alert setting: {error}")
            return
        if not self.auto_memory_alert:
            self.low_memory_samples = 0
            self.memory_pressure = False
            self.set_status("Memory pressure alerts are off.")
        else:
            self.set_status("Memory pressure alerts are on.")
        if self.last_snapshot:
            self._update_home(self.last_snapshot)

    def _open_task_manager(self) -> None:
        try:
            subprocess.Popen(["taskmgr.exe"], close_fds=True)
        except OSError as error:
            self.show_error(f"Could not open Task Manager: {error}")

    def _render_memory(self) -> None:
        self._section_heading(
            self.page_body,
            "Free memory on your terms",
            "Review open desktop apps, then ask selected apps to close normally. Save your work first; each app can show its own save prompt.",
        )

        note = self._card(self.page_body, padx=16, pady=12)
        note.pack(fill="x", pady=(0, 12))
        tk.Label(
            note,
            text="Only visible apps running under your account are listed. Known core Windows processes are excluded. Use Ctrl-click to select multiple apps; nothing is force-stopped.",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=800,
        ).pack(fill="x")

        listing = self._card(self.page_body, padx=13, pady=11)
        listing.pack(fill="both", expand=True)
        header = tk.Frame(listing, bg=SURFACE)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(
            header,
            text="Apps using the most memory",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
            anchor="w",
        ).pack(side="left")
        self._button(header, "Refresh", self._refresh_ram_apps, compact=True).pack(
            side="right"
        )

        self.ram_summary_var = tk.StringVar(value="Scanning for eligible desktop apps...")
        tk.Label(
            listing,
            textvariable=self.ram_summary_var,
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 7))
        columns = ("app", "window", "memory", "windows", "pid")
        self.ram_tree = ttk.Treeview(
            listing, columns=columns, show="headings", selectmode="extended", height=12
        )
        for key, label in (
            ("app", "Application"),
            ("window", "Window title"),
            ("memory", "Memory"),
            ("windows", "Windows"),
            ("pid", "PID"),
        ):
            self.ram_tree.heading(key, text=label)
        self.ram_tree.column("app", width=170, anchor="w")
        self.ram_tree.column("window", width=330, anchor="w")
        self.ram_tree.column("memory", width=100, anchor="center", stretch=False)
        self.ram_tree.column("windows", width=75, anchor="center", stretch=False)
        self.ram_tree.column("pid", width=75, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.ram_tree.yview)
        self.ram_tree.configure(yscrollcommand=scroll.set)
        self.ram_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = tk.Frame(self.page_body, bg=BG)
        actions.pack(fill="x", pady=(11, 0))
        self._button(
            actions, "Ask selected apps to close", self._close_selected_ram_apps, primary=True
        ).pack(side="right")
        self._button(actions, "Clear selection", self._clear_ram_selection, compact=True).pack(
            side="left"
        )
        self._refresh_ram_apps()

    def _refresh_ram_apps(self) -> None:
        if self.busy:
            self.set_status("Wait for the current operation to finish.")
            return
        if self.current_page != "memory":
            return
        self.ram_summary_var.set("Scanning for eligible desktop apps...")
        self.set_status("Scanning visible apps running under your account...")
        self._start_job(ram_manager.list_background_apps, self._ram_apps_loaded)

    def _ram_apps_loaded(self, apps: Any) -> None:
        if not isinstance(apps, list):
            self.show_error("The memory scan returned an invalid result.")
            return
        self.ram_rows = {str(app["pid"]): app for app in apps}
        if self.current_page != "memory" or not hasattr(self, "ram_tree"):
            return
        self.ram_tree.delete(*self.ram_tree.get_children())
        for item_id, app in self.ram_rows.items():
            self.ram_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    app["name"],
                    _short(app["title"], 75),
                    _format_bytes(app["memory"]),
                    app["window_count"],
                    app["pid"],
                ),
            )
        if apps:
            self.ram_summary_var.set(
                f"{len(apps)} eligible apps • Memory shown is approximate; closing an app may not free its full working set."
            )
            self.set_status("Memory scan complete. Select only apps you are ready to close.")
        else:
            self.ram_summary_var.set(
                "No eligible visible desktop apps were found. Background services and Windows essentials are hidden."
            )
            self.set_status("No eligible desktop apps were found.")

    def _clear_ram_selection(self) -> None:
        if hasattr(self, "ram_tree") and self.ram_tree.winfo_exists():
            self.ram_tree.selection_remove(self.ram_tree.selection())

    def _close_selected_ram_apps(self) -> None:
        if self.busy:
            self.set_status("Wait for the current operation to finish.")
            return
        selected = self.ram_tree.selection() if hasattr(self, "ram_tree") else ()
        apps = [self.ram_rows[item] for item in selected if item in self.ram_rows]
        if not apps:
            self.set_status("Select at least one app first.")
            return
        if len(apps) > 8:
            self.set_status("Select no more than eight apps per close request.")
            self.show_error("For a clear review, select up to eight apps per request.")
            return
        names = [app["name"] for app in apps]
        preview = "\n".join(f"• {name}" for name in names)
        estimated = sum(int(app["memory"]) for app in apps)
        if not messagebox.askyesno(
            "Confirm app close",
            (
                f"Ask these {len(apps)} app(s) to close?\n{preview}\n\n"
                f"Approximate memory in the selected apps: {_format_bytes(estimated)}.\n\n"
                "Save your work first. Each app can show its own save prompt. "
                "PC Booster will not force-stop or restart anything."
            ),
            parent=self,
        ):
            return
        self.set_status("Sending normal close requests to selected apps...")
        self._start_job(
            lambda: ram_manager.request_graceful_close(apps),
            self._ram_close_finished,
        )

    def _ram_close_finished(self, result: Any) -> None:
        if not isinstance(result, dict):
            self.show_error("The app-close request returned an invalid result.")
            return
        requested = result.get("requested", [])
        skipped = result.get("skipped", [])
        failed = result.get("failed", [])
        self.set_status(
            f"Close request sent to {len(requested)} app(s). Apps may ask to save or may stay open."
        )
        if skipped or failed:
            details = []
            if skipped:
                details.append("No longer available or no longer eligible: " + ", ".join(skipped[:6]))
            if failed:
                details.append("Windows rejected the close request: " + ", ".join(failed[:6]))
            self.show_error("Some apps could not receive a close request.\n\n" + "\n".join(details))
        if self.current_page == "memory":
            self.after(1300, self._refresh_ram_apps)

    def _render_cleanup(self) -> None:
        self._section_heading(
            self.page_body,
            "Old temporary files",
            "We scan only your account's temporary folder. Review and select files; cleanup sends them to the Recycle Bin.",
        )

        card = self._card(self.page_body, padx=16, pady=14)
        card.pack(fill="x", pady=(0, 12))
        controls = tk.Frame(card, bg=SURFACE)
        controls.pack(fill="x")
        tk.Label(
            controls,
            text="Older than",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(side="left", padx=(0, 8))
        self.age_var = tk.IntVar(value=7)
        self.age_combo = ttk.Combobox(
            controls,
            textvariable=self.age_var,
            values=(3, 7, 14, 30),
            state="readonly",
            width=5,
            style="Dark.TCombobox",
            justify="center",
        )
        self.age_combo.bind("<<ComboboxSelected>>", self._cleanup_age_changed)
        self.age_combo.pack(side="left")
        tk.Label(
            controls, text="days", bg=SURFACE, fg=MUTED, font=(FONT, 9)
        ).pack(side="left", padx=(8, 16))
        self._button(controls, "Scan files", self._scan_temp, primary=True).pack(
            side="left"
        )
        self._button(controls, "Select all", self._select_all_clean, compact=True).pack(
            side="right", padx=(8, 0)
        )
        self._button(controls, "Clear selection", self._clear_clean_selection, compact=True).pack(
            side="right"
        )

        self.scan_summary_var = tk.StringVar(value="Scan to review files before cleanup.")
        tk.Label(
            card,
            textvariable=self.scan_summary_var,
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(fill="x", pady=(11, 0))

        listing = self._card(self.page_body, padx=12, pady=10)
        listing.pack(fill="both", expand=True)
        columns = ("name", "folder", "size")
        self.clean_tree = ttk.Treeview(
            listing, columns=columns, show="headings", selectmode="extended", height=15
        )
        self.clean_tree.heading("name", text="File name")
        self.clean_tree.heading("folder", text="Folder")
        self.clean_tree.heading("size", text="Size")
        self.clean_tree.column("name", width=220, anchor="w")
        self.clean_tree.column("folder", width=490, anchor="w")
        self.clean_tree.column("size", width=95, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.clean_tree.yview)
        self.clean_tree.configure(yscrollcommand=scroll.set)
        self.clean_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bottom = tk.Frame(self.page_body, bg=BG)
        bottom.pack(fill="x", pady=(11, 0))
        self._button(bottom, "Move selected to Recycle Bin", self._clean_selected, primary=True).pack(
            side="right"
        )
        tk.Label(
            bottom,
            text="Recent files are excluded. Windows may ask for confirmation for some large files.",
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(side="left", anchor="w")
        if self.scan_result:
            suffix = (
                " (Showing the first 1,000 results; more files may exist)"
                if self.scan_result.truncated
                else ""
            )
            self.scan_summary_var.set(
                f"Found {len(self.scan_result.files)} files older than {self.scan_days or 7} days • Displayed size "
                f"{_format_bytes(self.scan_result.bytes_total)} • {self.scan_result.scanned_root}{suffix}"
            )
            self._populate_clean_tree(self.scan_result)

    def _cleanup_age_changed(self, _event: Any = None) -> None:
        if not self.scan_result:
            return
        self.scan_result = None
        self.scan_days = None
        self.scan_rows = {}
        if hasattr(self, "clean_tree") and self.clean_tree.winfo_exists():
            self.clean_tree.delete(*self.clean_tree.get_children())
            self.scan_summary_var.set("The age filter changed. Scan again before cleanup.")
        self.set_status("Choose an age filter, then scan again.")

    def _scan_temp(self) -> None:
        if self.busy:
            self.set_status("Wait for the current operation to finish.")
            return
        days = int(self.age_var.get())
        self.set_status(f"Scanning temporary files older than {days} days...")
        self._start_job(
            lambda: scan_user_temp(days),
            lambda result: self._scan_finished(result, days),
        )

    def _scan_finished(self, result: Any, requested_days: int) -> None:
        if not isinstance(result, ScanResult):
            self.show_error("The scan returned an invalid result.")
            return
        if int(self.age_var.get()) != requested_days:
            self.scan_result = None
            self.scan_days = None
            self.scan_rows = {}
            if self.current_page == "cleanup" and hasattr(self, "clean_tree"):
                self.clean_tree.delete(*self.clean_tree.get_children())
                self.scan_summary_var.set("The age filter changed during the scan. Scan again.")
            self.set_status("The age filter changed before the scan finished. Scan the new range.")
            return
        self.scan_result = result
        self.scan_days = requested_days
        self._populate_clean_tree(result)
        suffix = " (Showing the first 1,000 results; more files may exist)" if result.truncated else ""
        self.scan_summary_var.set(
            f"Found {len(result.files)} files older than {requested_days} days • Displayed size "
            f"{_format_bytes(result.bytes_total)} • {result.scanned_root}{suffix}"
        )
        self.set_status("Scan complete. Select files to move to the Recycle Bin.")

    def _populate_clean_tree(self, result: ScanResult) -> None:
        if self.current_page != "cleanup" or not hasattr(self, "clean_tree"):
            return
        self.clean_tree.delete(*self.clean_tree.get_children())
        self.scan_rows = {}
        for index, (path, size) in enumerate(result.files):
            row_id = str(index)
            self.scan_rows[row_id] = (path, size)
            self.clean_tree.insert(
                "",
                "end",
                iid=row_id,
                values=(Path(path).name, str(Path(path).parent), _format_bytes(size)),
            )

    def _select_all_clean(self) -> None:
        if hasattr(self, "clean_tree"):
            self.clean_tree.selection_set(self.clean_tree.get_children())

    def _clear_clean_selection(self) -> None:
        if hasattr(self, "clean_tree"):
            self.clean_tree.selection_remove(self.clean_tree.selection())

    def _clean_selected(self) -> None:
        if self.busy:
            self.set_status("Wait for the current operation to finish.")
            return
        if not self.scan_result or not hasattr(self, "clean_tree"):
            self.set_status("Run a scan first.")
            return
        selected = self.clean_tree.selection()
        candidates = [self.scan_rows[item] for item in selected if item in self.scan_rows]
        if not candidates:
            self.set_status("Select at least one file.")
            return
        total = sum(size for _, size in candidates)
        if not messagebox.askyesno(
            "Confirm cleanup",
            (
                f"Move {len(candidates)} files ({_format_bytes(total)}) to the Recycle Bin?\n\n"
                "Files newer than the selected age or changed since the scan will be skipped."
            ),
            parent=self,
        ):
            return
        paths = [path for path, _ in candidates]
        root = self.scan_result.scanned_root
        days = int(self.age_var.get())
        self.set_status("Moving selected files to the Recycle Bin...")
        self._start_job(
            lambda: recycle_files(paths, root, days),
            self._clean_finished,
        )

    def _clean_finished(self, count: Any) -> None:
        self.scan_result = None
        self.scan_days = None
        self.scan_rows = {}
        if self.current_page == "cleanup" and hasattr(self, "clean_tree"):
            self.clean_tree.delete(*self.clean_tree.get_children())
            self.scan_summary_var.set("Cleanup complete. Scan again to review storage.")
        self.set_status(f"Moved {count} files to the Recycle Bin.")

    def _render_startup(self) -> None:
        self._section_heading(
            self.page_body,
            "Choose apps you do not need at sign-in",
            "Manage startup entries for your account only. Disabling an entry does not uninstall or close the app.",
        )

        active_card = self._card(self.page_body, padx=13, pady=11)
        active_card.pack(fill="both", expand=True, pady=(0, 12))
        active_header = tk.Frame(active_card, bg=SURFACE)
        active_header.pack(fill="x", pady=(0, 8))
        tk.Label(
            active_header,
            text="Currently starts with Windows",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
        ).pack(side="left")
        self._button(active_header, "Refresh list", self._refresh_startup, compact=True).pack(
            side="right"
        )
        self.active_startup_tree = ttk.Treeview(
            active_card,
            columns=("name", "command"),
            show="headings",
            selectmode="extended",
            height=8,
        )
        self.active_startup_tree.heading("name", text="Entry name")
        self.active_startup_tree.heading("command", text="Startup command (preview)")
        self.active_startup_tree.column("name", width=210, anchor="w")
        self.active_startup_tree.column("command", width=640, anchor="w")
        active_scroll = ttk.Scrollbar(
            active_card, orient="vertical", command=self.active_startup_tree.yview
        )
        self.active_startup_tree.configure(yscrollcommand=active_scroll.set)
        self.active_startup_tree.pack(side="left", fill="both", expand=True)
        active_scroll.pack(side="right", fill="y")
        active_actions = tk.Frame(active_card, bg=SURFACE)
        active_actions.pack(fill="x", pady=(9, 0))
        self._button(
            active_actions,
            "Disable selected",
            self._disable_startup,
            primary=True,
            compact=True,
        ).pack(side="left")
        tk.Label(
            active_actions,
            text="Selected apps will stop launching automatically at your next sign-in.",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(side="right")

        disabled_card = self._card(self.page_body, padx=13, pady=11)
        disabled_card.pack(fill="both", expand=True)
        disabled_header = tk.Frame(disabled_card, bg=SURFACE)
        disabled_header.pack(fill="x", pady=(0, 8))
        tk.Label(
            disabled_header,
            text="Entries disabled by PC Booster",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
        ).pack(side="left")
        self.disabled_startup_tree = ttk.Treeview(
            disabled_card,
            columns=("name", "command"),
            show="headings",
            selectmode="extended",
            height=4,
        )
        self.disabled_startup_tree.heading("name", text="Entry name")
        self.disabled_startup_tree.heading("command", text="Saved startup command")
        self.disabled_startup_tree.column("name", width=210, anchor="w")
        self.disabled_startup_tree.column("command", width=640, anchor="w")
        disabled_scroll = ttk.Scrollbar(
            disabled_card, orient="vertical", command=self.disabled_startup_tree.yview
        )
        self.disabled_startup_tree.configure(yscrollcommand=disabled_scroll.set)
        self.disabled_startup_tree.pack(side="left", fill="both", expand=True)
        disabled_scroll.pack(side="right", fill="y")
        self._button(
            disabled_card,
            "Restore selected",
            self._restore_startup,
            compact=True,
        ).pack(side="left", pady=(8, 0))
        self._refresh_startup()

    def _refresh_startup(self) -> None:
        try:
            active = startup.list_active()
            disabled = startup.list_disabled()
        except OSError as error:
            self.show_error(f"Could not read startup entries: {error}")
            return
        self.active_startup_tree.delete(*self.active_startup_tree.get_children())
        self.disabled_startup_tree.delete(*self.disabled_startup_tree.get_children())
        self.startup_active_rows = {}
        self.startup_disabled_rows = {}
        for index, item in enumerate(active):
            row_id = f"active-{index}"
            self.startup_active_rows[row_id] = item["name"]
            self.active_startup_tree.insert(
                "", "end", iid=row_id, values=(item["name"], _short(item["command"]))
            )
        for index, item in enumerate(disabled):
            row_id = f"disabled-{index}"
            self.startup_disabled_rows[row_id] = item["name"]
            self.disabled_startup_tree.insert(
                "", "end", iid=row_id, values=(item["name"], _short(item["command"]))
            )
        if not active:
            self.set_status("No startup entries were found for your account.")

    def _disable_startup(self) -> None:
        selected = self.active_startup_tree.selection()
        names = [self.startup_active_rows[row] for row in selected if row in self.startup_active_rows]
        if not names:
            self.set_status("Select at least one startup entry.")
            return
        preview = "\n".join(f"• {name}" for name in names[:8])
        if len(names) > 8:
            preview += f"\n• and {len(names) - 8} more"
        if not messagebox.askyesno(
            "Confirm startup change",
            (
                f"Selected entries ({len(names)}):\n{preview}\n\n"
                "These apps will no longer start automatically at sign-in. They will not be closed now. "
                "You can restore them from the disabled list."
            ),
            parent=self,
        ):
            return
        done, errors = [], []
        for name in names:
            try:
                startup.disable(name)
                done.append(name)
            except (OSError, ValueError) as error:
                errors.append(f"{name}: {error}")
        self._refresh_startup()
        if errors:
            self.show_error("Some entries could not be changed:\n" + "\n".join(errors[:6]))
        if done:
            self.set_status(f"Disabled {len(done)} startup entries. You can restore them at any time.")

    def _restore_startup(self) -> None:
        selected = self.disabled_startup_tree.selection()
        names = [
            self.startup_disabled_rows[row]
            for row in selected
            if row in self.startup_disabled_rows
        ]
        if not names:
            self.set_status("Select a disabled entry to restore.")
            return
        done, errors = [], []
        for name in names:
            try:
                startup.restore(name)
                done.append(name)
            except (OSError, ValueError) as error:
                errors.append(f"{name}: {error}")
        self._refresh_startup()
        if errors:
            self.show_error("Some entries could not be restored:\n" + "\n".join(errors[:6]))
        if done:
            self.set_status(f"Restored {len(done)} startup entries.")

    def _render_tweaks(self) -> None:
        self._render_background_settings()
        self._section_heading(
            self.page_body,
            "Small, transparent changes",
            "Choose individual changes or apply the suggested interface settings in one click. Original values are saved for restore.",
        )

        explanation = self._card(self.page_body, padx=16, pady=13)
        explanation.pack(fill="x", pady=(0, 13))
        tk.Label(
            explanation,
            text=(
                "This version does not disable Windows Update, Defender, system services, or the page file. "
                "These changes apply only to the current Windows account."
            ),
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=780,
        ).pack(fill="x")

        try:
            states = tweaks.current_states()
        except OSError as error:
            states = {}
            self.set_status(f"Could not read some Windows settings: {error}")

        self.tweak_vars = {}
        self.tweak_status_vars = {}
        self.tweak_status_labels = {}
        descriptions = {
            "animations": "Reduces minimize and maximize animation. Sign out and back in may be required.",
            "transparency": "Turns off transparency. You can restore the original value at any time.",
        }
        for name, item in tweaks.TWEAKS.items():
            row = self._card(self.page_body, padx=16, pady=13)
            row.pack(fill="x", pady=5)
            top = tk.Frame(row, bg=SURFACE)
            top.pack(fill="x")
            var = tk.BooleanVar(value=states.get(name, False))
            self.tweak_vars[name] = var
            check = ttk.Checkbutton(
                top,
                text=item["label"],
                variable=var,
                style="Dark.TCheckbutton",
            )
            check.pack(side="left")
            status = tk.StringVar(
                value="Applied" if states.get(name, False) else "Not applied"
            )
            self.tweak_status_vars[name] = status
            status_label = tk.Label(
                top, textvariable=status, bg=SURFACE, fg=ACCENT if states.get(name, False) else MUTED, font=(FONT, 8)
            )
            status_label.pack(side="right")
            self.tweak_status_labels[name] = status_label
            tk.Label(
                row,
                text=descriptions[name],
                bg=SURFACE,
                fg=MUTED,
                font=(FONT, 9),
                anchor="w",
                justify="left",
            ).pack(fill="x", pady=(7, 0))

        actions = tk.Frame(self.page_body, bg=BG)
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, "Apply selected", self._apply_tweaks, primary=True).pack(
            side="right"
        )
        self._button(
            actions,
            "Apply suggested UI settings",
            self._apply_recommended_tweaks,
            compact=True,
        ).pack(side="left", padx=(0, 9))
        self._button(actions, "Restore original values", self._restore_tweaks).pack(
            side="left", padx=(0, 9)
        )
        tk.Label(
            self.page_body,
            text="Restore data is saved locally in the app data folder.",
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(16, 0))

    def _apply_tweaks(self) -> None:
        selected = [name for name, variable in self.tweak_vars.items() if variable.get()]
        if not selected:
            self.set_status("Select at least one change.")
            return
        labels = "\n".join(f"• {tweaks.TWEAKS[name]['label']}" for name in selected)
        if not messagebox.askyesno(
            "Confirm Windows changes",
            f"Apply these changes to your account?\n{labels}\n\n"
            "Original values are saved first and can be restored from this page.",
            parent=self,
        ):
            return
        done, errors = [], []
        for name in selected:
            try:
                tweaks.apply_selected([name])
                done.append(name)
            except (OSError, ValueError, TypeError) as error:
                errors.append(f"{tweaks.TWEAKS[name]['label']}: {error}")
        self._refresh_tweak_status()
        if errors:
            self.show_error("Some changes could not be applied:\n" + "\n".join(errors))
        if done:
            self.set_status(f"Applied {len(done)} changes. Original values are saved for restore.")

    def _apply_recommended_tweaks(self) -> None:
        for variable in self.tweak_vars.values():
            variable.set(True)
        self._apply_tweaks()

    def _restore_tweaks(self) -> None:
        if not messagebox.askyesno(
            "Restore your settings",
            "Restore the values PC Booster saved before making changes?",
            parent=self,
        ):
            return
        restored = tweaks.restore_original()
        self._refresh_tweak_status()
        if restored:
            self.set_status(f"Restored {len(restored)} original values.")
        else:
            self.set_status("There are no saved changes to restore.")

    def _refresh_tweak_status(self) -> None:
        try:
            states = tweaks.current_states()
        except OSError as error:
            self.show_error(f"Could not read tweak status: {error}")
            return
        for name, variable in self.tweak_status_vars.items():
            applied = states.get(name, False)
            variable.set("Applied" if applied else "Not applied")
            if name in self.tweak_status_labels:
                self.tweak_status_labels[name].configure(fg=ACCENT if applied else MUTED)

    def _render_background_settings(self) -> None:
        self._section_heading(
            self.page_body,
            "Background & startup",
            "Keep PC Booster available from the system tray and choose whether it opens when you sign in.",
        )
        card = self._card(self.page_body, padx=16, pady=13)
        card.pack(fill="x", pady=(0, 18))
        try:
            startup_enabled = autostart.is_enabled()
        except OSError as error:
            startup_enabled = False
            self.set_status(f"Could not read startup setting: {error}")

        self.startup_var = tk.BooleanVar(value=startup_enabled)
        ttk.Checkbutton(
            card,
            text="Start PC Booster when I sign in to Windows",
            variable=self.startup_var,
            command=self._toggle_startup,
            style="Dark.TCheckbutton",
        ).pack(anchor="w")
        tk.Label(
            card,
            text="Starts minimized to the tray. This setting applies only to your Windows account.",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=(25, 0), pady=(4, 12))

        self.tray_var = tk.BooleanVar(value=self.close_to_tray)
        ttk.Checkbutton(
            card,
            text="Keep running in the tray when I close or minimize the window",
            variable=self.tray_var,
            command=self._toggle_close_to_tray,
            style="Dark.TCheckbutton",
        ).pack(anchor="w")
        tk.Label(
            card,
            text="Use the tray icon's Exit command to quit completely. Monitoring slows down while hidden.",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=(25, 0), pady=(4, 0))

    def _toggle_startup(self) -> None:
        enabled = self.startup_var.get()
        try:
            autostart.set_enabled(enabled)
        except (OSError, ValueError) as error:
            self.startup_var.set(not enabled)
            self.show_error(f"Could not update the Windows sign-in setting:\n{error}")
            return
        self.set_status(
            "PC Booster will start minimized at sign-in."
            if enabled
            else "PC Booster will no longer start at sign-in."
        )

    def _toggle_close_to_tray(self) -> None:
        self.close_to_tray = self.tray_var.get()
        prefs = read_json("settings.json", {})
        if not isinstance(prefs, dict):
            prefs = {}
        prefs["close_to_tray"] = self.close_to_tray
        try:
            write_json("settings.json", prefs)
        except OSError as error:
            self.close_to_tray = not self.close_to_tray
            self.tray_var.set(self.close_to_tray)
            self.show_error(f"Could not save the tray setting:\n{error}")
            return
        self.set_status(
            "The window will stay available in the system tray."
            if self.close_to_tray
            else "Closing the window will quit PC Booster."
        )

    def _ensure_tray(self) -> bool:
        if self.tray_icon is not None:
            return True
        icon = None
        try:
            import pystray
            from PIL import Image

            with Image.open(_asset_path("pc_booster.ico")) as source:
                tray_image = source.convert("RGBA").copy()
            menu = pystray.Menu(
                pystray.MenuItem("Open PC Booster", self._tray_open, default=True),
                pystray.MenuItem("Exit PC Booster", self._tray_exit),
            )
            icon = pystray.Icon("pc_booster", tray_image, "PC Booster", menu)
            self.tray_icon = icon
            icon.run_detached()
            return True
        except Exception as error:
            if icon is not None:
                try:
                    icon.stop()
                except Exception:
                    pass
            self.tray_icon = None
            self.set_status(f"System tray is unavailable: {error}")
            return False

    def _tray_open(self, _icon: Any, _item: Any) -> None:
        self.after(0, self._restore_from_tray)

    def _tray_exit(self, _icon: Any, _item: Any) -> None:
        self.after(0, self._close)

    def _restore_from_tray(self) -> None:
        if self.closing:
            return
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _hide_to_tray(self) -> None:
        if self._ensure_tray():
            self.withdraw()
        else:
            self.deiconify()
            self.state("normal")
            self.lift()

    def _on_unmap(self, _event: tk.Event) -> None:
        if self.close_to_tray and self.state() == "iconic":
            self.after_idle(self._hide_minimized_window)

    def _hide_minimized_window(self) -> None:
        if self.state() == "iconic" and self._ensure_tray():
            self.withdraw()

    def _request_close(self) -> None:
        if not self.close_to_tray:
            self._close()
            return
        if self._ensure_tray():
            self.withdraw()
            return
        if messagebox.askyesno(
            "System tray unavailable",
            "PC Booster could not create a tray icon. Exit the app instead?",
            parent=self,
        ):
            self._close()

    def _tick(self) -> None:
        if self.closing:
            return
        if not self.monitor_busy:
            self.monitor_busy = True

            def sample() -> None:
                try:
                    self.monitor_pending.put((self.monitor.sample(), None))
                except Exception as error:
                    self.monitor_pending.put((None, error))

            threading.Thread(target=sample, name="PCBoosterMonitor", daemon=True).start()
        interval = 8000 if self.state() == "withdrawn" else 2000
        self.after(interval, self._tick)

    def _track_memory_pressure(self, data: dict[str, Any]) -> None:
        if not self.auto_memory_alert:
            self.low_memory_samples = 0
            self.memory_pressure = False
            return
        was_under_pressure = self.memory_pressure
        available = data["memory_available"] / max(data["memory_total"], 1)
        if self.memory_pressure:
            if available >= 0.14:
                self.memory_pressure = False
                self.low_memory_samples = 0
                self.set_status("Available memory has recovered.")
                self._notify_tray("Available memory has recovered.")
            return
        if available <= 0.10:
            self.low_memory_samples += 1
            if self.low_memory_samples >= 3:
                self.memory_pressure = True
                if not was_under_pressure:
                    self.set_status("Alert: available memory is low. Review the highest-usage apps.")
                    self._notify_tray(
                        "Available memory is low. Open PC Booster to review high-usage apps."
                    )
        else:
            self.low_memory_samples = 0

    def _start_job(self, operation: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        if self.busy:
            self.set_status("Wait for the current operation to finish.")
            return
        self.busy = True

        def run() -> None:
            try:
                self.pending.put((callback, operation(), None))
            except Exception as error:
                self.pending.put((callback, None, error))

        threading.Thread(target=run, name="PCBoosterWorker", daemon=True).start()

    def _notify_tray(self, message: str) -> None:
        icon = self.tray_icon
        if self.state() != "withdrawn" or icon is None:
            return
        if not getattr(icon, "HAS_NOTIFICATION", False):
            return
        try:
            icon.notify(message, "PC Booster")
        except Exception:
            pass

    def _poll_pending(self) -> None:
        try:
            while True:
                callback, result, error = self.pending.get_nowait()
                self.busy = False
                if error is not None:
                    self.set_status("The operation did not complete.")
                    if self.current_page == "cleanup" and hasattr(self, "clean_tree"):
                        self.scan_result = None
                        self.scan_days = None
                        self.scan_rows = {}
                        self.clean_tree.delete(*self.clean_tree.get_children())
                        self.scan_summary_var.set("Scan did not complete. Scan again before cleanup.")
                    elif self.current_page != "cleanup":
                        self.scan_result = None
                        self.scan_days = None
                        self.scan_rows = {}
                    self.show_error(str(error))
                else:
                    callback(result)
        except queue.Empty:
            pass
        try:
            while True:
                result, error = self.monitor_pending.get_nowait()
                self.monitor_busy = False
                if error is not None:
                    self.set_status(f"Could not refresh some readings: {error}")
                elif result is not None:
                    self.last_snapshot = result
                    self._track_memory_pressure(result)
                    self._update_home(result)
        except queue.Empty:
            pass
        if not self.closing:
            self.after(120, self._poll_pending)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def show_error(self, text: str) -> None:
        messagebox.showerror("PC Booster", text, parent=self)

    def _close(self) -> None:
        if self.closing:
            return
        if self.busy:
            self._restore_from_tray()
            messagebox.showinfo(
                "Operation in progress",
                "Wait for the current operation to finish before closing the app.",
                parent=self,
            )
            return
        self.closing = True
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        if not self.monitor_busy:
            self.monitor.close()
        self.destroy()
