from __future__ import annotations

import collections
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import __version__
from . import autostart
from .cleaner_pro import CleanCategory, clean_categories, scan_clean_categories
from .localization import tr
from .monitor import SystemMonitor
from . import ram_manager, startup, tweaks
from .sentinel import AutoPilotSentinel
from .specs import get_full_system_specs
from .state import get_settings, save_settings, write_json


# 2026 Cyber-Obsidian Design Palette
BG = "#080d10"
SIDEBAR = "#0c1519"
SURFACE = "#111c22"
SURFACE_ALT = "#16252d"
BORDER = "#1e333d"
BORDER_GLOW = "#00f5a0"
TEXT = "#e8f2ef"
MUTED = "#859f97"
ACCENT = "#00f5a0"
ACCENT_CYAN = "#00d9f5"
ACCENT_DARK = "#0d2b23"
WARN = "#ffb800"
WARN_BG = "#2c210e"
DANGER = "#ff4757"
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


def _short(value: str, limit: int = 120) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


class CircularGauge(tk.Canvas):
    """High-performance vector circular gauge widget drawn on tk.Canvas."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        size: int = 145,
        thickness: int = 11,
        bg: str = SURFACE,
    ) -> None:
        super().__init__(
            parent,
            width=size,
            height=size + 24,
            bg=bg,
            bd=0,
            highlightthickness=0,
        )
        self.size = size
        self.thickness = thickness
        self.title_text = title
        self.percent = 0.0
        self.detail_text = ""
        self._center = size // 2
        self._radius = (size - thickness - 8) // 2
        self._draw()

    def set_value(self, percent: float, detail: str = "") -> None:
        self.percent = max(0.0, min(100.0, float(percent)))
        self.detail_text = detail
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = self._center
        r = self._radius
        t = self.thickness

        bbox = (c - r, c - r, c + r, c + r)

        # 270-degree sweep from 135 deg to 405 deg (leaves bottom open)
        start_angle = 135
        sweep = 270

        # Background track
        self.create_arc(
            bbox,
            start=start_angle,
            extent=-sweep,
            style="arc",
            width=t,
            outline=BORDER,
        )

        # Dynamic arc color based on load
        if self.percent < 68:
            color = ACCENT
        elif self.percent < 86:
            color = WARN
        else:
            color = DANGER

        # Foreground active arc
        active_extent = -(sweep * (self.percent / 100.0))
        if abs(active_extent) > 0.5:
            self.create_arc(
                bbox,
                start=start_angle,
                extent=active_extent,
                style="arc",
                width=t,
                outline=color,
            )

        # Center Text: Large Percentage
        self.create_text(
            c,
            c - 6,
            text=f"{self.percent:.0f}%",
            fill=TEXT,
            font=(FONT, 16, "bold"),
        )

        # Title Subtitle
        self.create_text(
            c,
            c + 16,
            text=self.title_text,
            fill=MUTED,
            font=(FONT, 8, "bold"),
        )

        # Bottom detail
        if self.detail_text:
            self.create_text(
                c,
                self.size + 10,
                text=_short(self.detail_text, 28),
                fill=MUTED,
                font=(FONT, 8),
            )


class PCBooster(tk.Tk):
    def __init__(self, start_in_background: bool = False) -> None:
        super().__init__()
        self.settings = get_settings()
        self.lang = self.settings.get("language", "en")

        if start_in_background:
            self.withdraw()

        self.title("PC Booster Pro")
        self.geometry("1180x820")
        self.minsize(1080, 720)
        self.configure(bg=BG)
        self.option_add("*Font", (FONT, 10))

        try:
            self.iconbitmap(str(_asset_path("pc_booster.ico")))
        except tk.TclError:
            pass

        self.style = ttk.Style(self)
        self._configure_style()

        self.start_in_background = start_in_background
        self.tray_icon: Any | None = None
        self.monitor = SystemMonitor()
        self.sentinel = AutoPilotSentinel()
        self.sentinel.start()

        self.last_snapshot: dict[str, Any] | None = None
        self.busy = False
        self.monitor_busy = False
        self.closing = False
        self.current_page = "home"

        # Queues for background work
        self.pending: queue.Queue[tuple[Callable[..., None], Any, Exception | None]] = queue.Queue()
        self.monitor_pending: queue.Queue[tuple[dict[str, Any] | None, Exception | None]] = queue.Queue()

        # State data
        self.clean_categories_data: list[CleanCategory] = []
        self.clean_cat_vars: dict[str, tk.BooleanVar] = {}
        self.ram_rows: dict[str, dict[str, Any]] = {}
        self.startup_active_rows: dict[str, str] = {}
        self.startup_disabled_rows: dict[str, str] = {}
        self.tweak_vars: dict[str, tk.BooleanVar] = {}
        self.tweak_status_vars: dict[str, tk.StringVar] = {}
        self.tweak_status_labels: dict[str, tk.Label] = {}
        self.gauges: dict[str, CircularGauge] = {}

        self._build_shell()
        self.show_page("home")

        self.after(120, self._poll_pending)
        self.after(800, self._tick)

        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.bind("<Unmap>", self._on_unmap)

        if self.start_in_background:
            self.after(0, self._hide_to_tray)

    def _t(self, key: str) -> str:
        return tr(key, self.lang)

    def _configure_style(self) -> None:
        self.style.theme_use("clam")
        self.style.configure(
            "Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=32,
            borderwidth=0,
            font=(FONT, 9),
        )
        self.style.configure(
            "Treeview.Heading",
            background=SURFACE_ALT,
            foreground=ACCENT_CYAN,
            relief="flat",
            font=(FONT, 9, "bold"),
        )
        self.style.map(
            "Treeview",
            background=[("selected", ACCENT_DARK)],
            foreground=[("selected", TEXT)],
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
            width=9,
        )
        self.style.map("Dark.Vertical.TScrollbar", background=[("active", ACCENT_DARK)])

    def _build_shell(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        # Left Navigation Sidebar
        self.sidebar = tk.Frame(shell, bg=SIDEBAR, width=238, highlightthickness=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # Right Main Workspace
        main = tk.Frame(shell, bg=BG)
        main.pack(side="right", fill="both", expand=True)

        # Top Header Bar
        header = tk.Frame(main, bg=BG, padx=26, pady=12)
        header.pack(fill="x")

        header_left = tk.Frame(header, bg=BG)
        header_left.pack(side="left", anchor="w")
        tk.Label(
            header_left,
            text="PC BOOSTER PRO",
            bg=BG,
            fg=ACCENT,
            font=(FONT, 9, "bold"),
        ).pack(anchor="w")
        self.page_title = tk.Label(
            header_left,
            text="",
            bg=BG,
            fg=TEXT,
            font=(FONT, 18, "bold"),
        )
        self.page_title.pack(anchor="w", pady=(2, 0))

        header_right = tk.Frame(header, bg=BG)
        header_right.pack(side="right", anchor="center")

        # Language Switcher Toggle
        lang_btn = tk.Button(
            header_right,
            text="🌐 عربي" if self.lang == "en" else "🌐 English",
            command=self._toggle_language,
            bg=SURFACE_ALT,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=ACCENT,
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            font=(FONT, 9, "bold"),
            cursor="hand2",
        )
        lang_btn.pack(side="right", padx=(8, 0))
        self.lang_btn = lang_btn

        # Sentinel Pulse Badge
        self.sentinel_badge = tk.Frame(header_right, bg=ACCENT_DARK, padx=12, pady=6)
        self.sentinel_badge.pack(side="right")
        self.sentinel_dot = tk.Label(
            self.sentinel_badge, text="●", bg=ACCENT_DARK, fg=ACCENT, font=(FONT, 9)
        )
        self.sentinel_dot.pack(side="left", padx=(0, 6))
        self.sentinel_badge_text = tk.Label(
            self.sentinel_badge,
            text=self._t("live_monitoring"),
            bg=ACCENT_DARK,
            fg=TEXT,
            font=(FONT, 9, "bold"),
        )
        self.sentinel_badge_text.pack(side="left")

        # Bottom Status Bar
        footer = tk.Frame(main, bg=BG, padx=26, pady=10)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))
        self.status_var = tk.StringVar(value="PC Booster Pro active. Zero-Kill Protection Engaged.")
        self.status_label = tk.Label(
            footer, textvariable=self.status_var, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w"
        )
        self.status_label.pack(fill="x")

        # Scrollable Page Canvas
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

        self.page_body = tk.Frame(self.page_canvas, bg=BG, padx=26, pady=4)
        self.page_body_window = self.page_canvas.create_window(
            (0, 0), window=self.page_body, anchor="nw"
        )
        self.page_body.bind("<Configure>", self._sync_scroll_region)
        self.page_canvas.bind("<Configure>", self._resize_page_body)

    def _sync_scroll_region(self, _event: Any = None) -> None:
        self.page_canvas.configure(scrollregion=self.page_canvas.bbox("all"))

    def _resize_page_body(self, event: tk.Event) -> None:
        self.page_canvas.itemconfigure(self.page_body_window, width=event.width)

    def _toggle_language(self) -> None:
        self.lang = "ar" if self.lang == "en" else "en"
        self.settings["language"] = self.lang
        save_settings(self.settings)
        self.lang_btn.configure(text="🌐 عربي" if self.lang == "en" else "🌐 English")
        self.sentinel_badge_text.configure(text=self._t("live_monitoring"))
        self._build_sidebar()
        self.show_page(self.current_page)

    def _build_sidebar(self) -> None:
        for child in self.sidebar.winfo_children():
            child.destroy()

        brand = tk.Frame(self.sidebar, bg=SIDEBAR, padx=18, pady=20)
        brand.pack(fill="x")

        mark = tk.Canvas(brand, width=34, height=34, bg=SIDEBAR, highlightthickness=0)
        mark.create_oval(2, 2, 32, 32, outline=ACCENT, width=2)
        mark.create_arc(8, 8, 26, 26, start=45, extent=290, outline=ACCENT_CYAN, width=3, style="arc")
        mark.pack(side="left", padx=(0, 10))

        title = tk.Frame(brand, bg=SIDEBAR)
        title.pack(side="left")
        tk.Label(title, text="PC Booster", bg=SIDEBAR, fg=TEXT, font=(FONT, 12, "bold")).pack(anchor="w")
        tk.Label(title, text="2026 Pro Suite", bg=SIDEBAR, fg=ACCENT, font=(FONT, 8, "bold")).pack(
            anchor="w", pady=(1, 0)
        )

        tk.Frame(self.sidebar, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(0, 14))

        self.nav_buttons: dict[str, tk.Button] = {}
        links = [
            ("home", self._t("nav_overview")),
            ("cleaner", self._t("nav_cleaner")),
            ("memory", self._t("nav_memory")),
            ("startup", self._t("nav_startup")),
            ("tweaks", self._t("nav_tweaks")),
            ("specs", self._t("nav_specs")),
            ("settings", self._t("nav_settings")),
        ]

        for key, label in links:
            button = tk.Button(
                self.sidebar,
                text=f"  {label}",
                anchor="w",
                padx=16,
                pady=11,
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
            button.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[key] = button

        footer = tk.Frame(self.sidebar, bg=SIDEBAR, padx=16, pady=16)
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=(0, 10))
        tk.Label(
            footer,
            text=f"Version {__version__} Pro",
            bg=SIDEBAR,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            footer,
            text="Autonomous Zero-Kill Engine",
            bg=SIDEBAR,
            fg=ACCENT,
            font=(FONT, 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))

    def _card(self, parent: tk.Misc, padx: int = 16, pady: int = 14) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            padx=padx,
            pady=pady,
            highlightthickness=1,
            highlightbackground=BORDER,
        )

    def _button(
        self,
        parent: tk.Misc,
        label: str,
        command: Callable[[], None],
        primary: bool = False,
        compact: bool = False,
        color: str | None = None,
    ) -> tk.Button:
        bg_color = color or (ACCENT_DARK if primary else SURFACE_ALT)
        fg_color = BG if color else (ACCENT if primary else TEXT)
        button = tk.Button(
            parent,
            text=label,
            command=command,
            bg=bg_color,
            fg=fg_color,
            activebackground=ACCENT if primary else BORDER,
            activeforeground=BG if primary else TEXT,
            relief="flat",
            bd=0,
            padx=14 if compact else 18,
            pady=8 if compact else 10,
            font=(FONT, 9 if compact else 10, "bold"),
            cursor="hand2",
            takefocus=True,
        )
        return button

    def _section_heading(self, parent: tk.Misc, title: str, description: str = "") -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", pady=(0, 10))
        tk.Label(frame, text=title, bg=BG, fg=TEXT, font=(FONT, 15, "bold"), anchor="w").pack(fill="x")
        if description:
            tk.Label(
                frame, text=description, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w"
            ).pack(fill="x", pady=(4, 0))
        return frame

    def show_page(self, page: str) -> None:
        self.current_page = page
        self.page_canvas.yview_moveto(0)
        for child in self.page_body.winfo_children():
            child.destroy()

        titles = {
            "home": self._t("nav_overview"),
            "cleaner": self._t("nav_cleaner"),
            "memory": self._t("nav_memory"),
            "startup": self._t("nav_startup"),
            "tweaks": self._t("nav_tweaks"),
            "specs": self._t("nav_specs"),
            "settings": self._t("nav_settings"),
        }
        self.page_title.configure(text=titles.get(page, "PC Booster Pro"))

        for key, button in self.nav_buttons.items():
            active = key == page
            button.configure(
                bg=ACCENT_DARK if active else SIDEBAR,
                fg=ACCENT if active else MUTED,
                font=(FONT, 10, "bold" if active else "normal"),
            )

        if page == "home":
            self._render_home()
        elif page == "cleaner":
            self._render_cleaner()
        elif page == "memory":
            self._render_memory()
        elif page == "startup":
            self._render_startup()
        elif page == "tweaks":
            self._render_tweaks()
        elif page == "specs":
            self._render_specs()
        elif page == "settings":
            self._render_settings()

    # =========================================================================
    # PAGE 1: OVERVIEW & VECTOR GAUGES & SENTINEL HUD
    # =========================================================================

    def _render_home(self) -> None:
        # Hero Action Banner: 1-Click Quick Boost
        hero = self._card(self.page_body, padx=18, pady=14)
        hero.pack(fill="x", pady=(0, 10))

        hero_left = tk.Frame(hero, bg=SURFACE)
        hero_left.pack(side="left", fill="both", expand=True)

        tk.Label(
            hero_left,
            text=self._t("quick_boost_btn"),
            bg=SURFACE,
            fg=ACCENT,
            font=(FONT, 13, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            hero_left,
            text=self._t("quick_boost_desc"),
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            wraplength=600,
        ).pack(fill="x", pady=(4, 0))

        hero_right = tk.Frame(hero, bg=SURFACE)
        hero_right.pack(side="right")
        self._button(
            hero_right,
            f"⚡ {self._t('quick_boost_btn')}",
            self._trigger_quick_boost,
            primary=True,
            color=ACCENT,
        ).pack()

        # Circular Donut Gauges Row
        gauge_card = self._card(self.page_body, padx=14, pady=12)
        gauge_card.pack(fill="x", pady=(0, 10))

        gauges_grid = tk.Frame(gauge_card, bg=SURFACE)
        gauges_grid.pack(fill="x")
        for i in range(4):
            gauges_grid.grid_columnconfigure(i, weight=1, uniform="gauge")

        self.gauges = {
            "cpu": CircularGauge(gauges_grid, self._t("cpu_metric")),
            "ram": CircularGauge(gauges_grid, self._t("ram_metric")),
            "disk": CircularGauge(gauges_grid, self._t("disk_metric")),
            "gpu": CircularGauge(gauges_grid, self._t("gpu_metric")),
        }
        self.gauges["cpu"].grid(row=0, column=0, padx=6)
        self.gauges["ram"].grid(row=0, column=1, padx=6)
        self.gauges["disk"].grid(row=0, column=2, padx=6)
        self.gauges["gpu"].grid(row=0, column=3, padx=6)

        # Sentinel Autonomous Control Card
        sentinel_card = self._card(self.page_body, padx=16, pady=12)
        sentinel_card.pack(fill="x", pady=(0, 10))

        s_head = tk.Frame(sentinel_card, bg=SURFACE)
        s_head.pack(fill="x", pady=(0, 8))
        tk.Label(
            s_head,
            text=self._t("sentinel_title"),
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 11, "bold"),
        ).pack(side="left")

        stats = self.sentinel.get_stats()
        status_text = self._t("sentinel_active") if stats["enabled"] else self._t("sentinel_idle")
        tk.Label(
            s_head,
            text=f"● {status_text}",
            bg=SURFACE,
            fg=ACCENT if stats["enabled"] else MUTED,
            font=(FONT, 9, "bold"),
        ).pack(side="right")

        # 3 Real-time Stat Chips
        chips_frame = tk.Frame(sentinel_card, bg=SURFACE)
        chips_frame.pack(fill="x", pady=(0, 10))
        for col in range(3):
            chips_frame.grid_columnconfigure(col, weight=1, uniform="chip")

        self.chip_turbo = self._make_chip(
            chips_frame, 0, "⚡ " + self._t("sentinel_turbo_active"), stats["active_boosted_app"]
        )
        self.chip_tamed = self._make_chip(
            chips_frame, 1, "💤 " + self._t("sentinel_tamed_count"), f"{stats['tamed_count']} instances"
        )
        self.chip_freed = self._make_chip(
            chips_frame, 2, "🧹 " + self._t("sentinel_ram_freed"), _format_bytes(stats["total_freed_bytes"])
        )

        # Live Event HUD Terminal
        tk.Label(
            sentinel_card,
            text=f"📜 {self._t('recent_activity')}:",
            bg=SURFACE,
            fg=ACCENT_CYAN,
            font=(FONT, 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(4, 4))

        self.event_hud = tk.Frame(sentinel_card, bg=BG, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER)
        self.event_hud.pack(fill="x")
        self._update_event_hud()

        # Top Processes Card
        process_card = self._card(self.page_body, padx=14, pady=10)
        process_card.pack(fill="both", expand=True)

        process_header = tk.Frame(process_card, bg=SURFACE)
        process_header.pack(fill="x", pady=(0, 6))
        tk.Label(
            process_header,
            text="Active Heavy Processes",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
            anchor="w",
        ).pack(side="left")
        self._button(process_header, "Task Manager", self._open_task_manager, compact=True).pack(side="right")

        columns = ("name", "cpu", "memory")
        self.process_tree = ttk.Treeview(process_card, columns=columns, show="headings", height=5, selectmode="none")
        self.process_tree.heading("name", text="Application")
        self.process_tree.heading("cpu", text="CPU")
        self.process_tree.heading("memory", text="RAM Working-Set")
        self.process_tree.column("name", width=460, anchor="w")
        self.process_tree.column("cpu", width=100, anchor="center", stretch=False)
        self.process_tree.column("memory", width=140, anchor="center", stretch=False)

        process_scroll = ttk.Scrollbar(process_card, orient="vertical", command=self.process_tree.yview)
        self.process_tree.configure(yscrollcommand=process_scroll.set)
        self.process_tree.pack(side="left", fill="both", expand=True)
        process_scroll.pack(side="right", fill="y")

        if self.last_snapshot:
            self._update_home(self.last_snapshot)

    def _make_chip(self, parent: tk.Misc, col: int, title: str, value: str) -> tk.Label:
        chip = tk.Frame(parent, bg=SURFACE_ALT, padx=12, pady=8, highlightthickness=1, highlightbackground=BORDER)
        chip.grid(row=0, column=col, sticky="nsew", padx=4)
        tk.Label(chip, text=title, bg=SURFACE_ALT, fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        lbl = tk.Label(chip, text=value, bg=SURFACE_ALT, fg=TEXT, font=(FONT, 11, "bold"), anchor="w")
        lbl.pack(anchor="w", pady=(2, 0))
        return lbl

    def _update_event_hud(self) -> None:
        if not hasattr(self, "event_hud") or not self.event_hud.winfo_exists():
            return
        for child in self.event_hud.winfo_children():
            child.destroy()

        events = self.sentinel.get_stats()["events"][:4]
        if not events:
            tk.Label(
                self.event_hud,
                text="Watching in silence. No runaway processes detected.",
                bg=BG,
                fg=MUTED,
                font=(FONT, 8),
            ).pack(anchor="w")
            return

        for ev in events:
            row = tk.Frame(self.event_hud, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{ev['icon']} [{ev['time']}]", bg=BG, fg=ACCENT, font=(FONT, 8, "bold")).pack(side="left")
            tk.Label(row, text=f" {ev['title']}: {ev['detail']}", bg=BG, fg=TEXT, font=(FONT, 8)).pack(side="left")

    def _trigger_quick_boost(self) -> None:
        if self.busy:
            return
        self.set_status("Executing 1-Click Quick Boost...")
        self._start_job(self.sentinel.manual_quick_boost, self._on_quick_boost_done)

    def _on_quick_boost_done(self, result: Any) -> None:
        if isinstance(result, dict):
            freed_mb = result["ram_freed_bytes"] / (1024 * 1024)
            apps = result["apps_trimmed"]
            msg = f"✨ Quick Boost Complete: Freed {freed_mb:.1f} MB RAM across {apps} apps in {result['elapsed_seconds']:.2f}s!"
            self.set_status(msg)
            messagebox.showinfo("PC Booster Pro", msg, parent=self)
            self._update_event_hud()

    # =========================================================================
    # PAGE 2: MULTI-TARGET DEEP CLEANER
    # =========================================================================

    def _render_cleaner(self) -> None:
        self._section_heading(self.page_body, self._t("cleaner_title"), self._t("cleaner_desc"))

        top_card = self._card(self.page_body, padx=16, pady=12)
        top_card.pack(fill="x", pady=(0, 10))

        actions = tk.Frame(top_card, bg=SURFACE)
        actions.pack(fill="x")
        self._button(actions, self._t("scan_btn"), self._scan_cleaner, primary=True).pack(side="left", padx=(0, 8))
        self._button(actions, self._t("clean_btn"), self._clean_selected_targets, color=ACCENT_CYAN).pack(side="left", padx=(0, 8))
        self._button(actions, self._t("select_all"), self._select_all_clean_cats, compact=True).pack(side="right", padx=(6, 0))
        self._button(actions, self._t("clear_selection"), self._clear_clean_cats, compact=True).pack(side="right")

        self.clean_status_var = tk.StringVar(value="Click 'Scan System' to identify reclaimable space.")
        tk.Label(
            top_card,
            textvariable=self.clean_status_var,
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(10, 0))

        # Categories Container
        self.clean_items_frame = tk.Frame(self.page_body, bg=BG)
        self.clean_items_frame.pack(fill="both", expand=True)

        if not self.clean_categories_data:
            self._scan_cleaner()
        else:
            self._populate_clean_categories()

    def _scan_cleaner(self) -> None:
        if self.busy:
            return
        self.set_status("Scanning system cleaning targets...")
        self.clean_status_var.set("Scanning temporary folders, browser caches, and Recycle Bin...")
        self._start_job(scan_clean_categories, self._on_cleaner_scanned)

    def _on_cleaner_scanned(self, cats: Any) -> None:
        if isinstance(cats, list):
            self.clean_categories_data = cats
            total_b = sum(c.total_bytes for c in cats)
            self.clean_status_var.set(f"Scan complete. Reclaimable space found: {_format_bytes(total_b)}")
            self.set_status(f"Scan complete: {_format_bytes(total_b)} ready to clean.")
            self._populate_clean_categories()

    def _populate_clean_categories(self) -> None:
        if not hasattr(self, "clean_items_frame") or not self.clean_items_frame.winfo_exists():
            return
        for child in self.clean_items_frame.winfo_children():
            child.destroy()

        self.clean_cat_vars = {}
        for cat in self.clean_categories_data:
            row = self._card(self.clean_items_frame, padx=14, pady=10)
            row.pack(fill="x", pady=4)

            top = tk.Frame(row, bg=SURFACE)
            top.pack(fill="x")

            var = tk.BooleanVar(value=True if cat.total_bytes > 0 else False)
            self.clean_cat_vars[cat.id] = var

            chk = ttk.Checkbutton(top, text=cat.name, variable=var, style="Dark.TCheckbutton")
            chk.pack(side="left")

            size_badge = tk.Frame(top, bg=SURFACE_ALT, padx=8, pady=3)
            size_badge.pack(side="right")
            tk.Label(
                size_badge,
                text=f"{_format_bytes(cat.total_bytes)} ({cat.count} items)",
                bg=SURFACE_ALT,
                fg=ACCENT if cat.total_bytes > 0 else MUTED,
                font=(FONT, 8, "bold"),
            ).pack()

            tk.Label(
                row,
                text=cat.description,
                bg=SURFACE,
                fg=MUTED,
                font=(FONT, 8),
                anchor="w",
            ).pack(fill="x", padx=(24, 0), pady=(3, 0))

    def _select_all_clean_cats(self) -> None:
        for var in self.clean_cat_vars.values():
            var.set(True)

    def _clear_clean_cats(self) -> None:
        for var in self.clean_cat_vars.values():
            var.set(False)

    def _clean_selected_targets(self) -> None:
        if self.busy:
            return
        selected = {cid for cid, var in self.clean_cat_vars.items() if var.get()}
        if not selected:
            self.set_status("Select at least one cleaning target.")
            return

        total_b = sum(c.total_bytes for c in self.clean_categories_data if c.id in selected)
        if not messagebox.askyesno(
            "Confirm Clean",
            f"Safely clean selected targets ({_format_bytes(total_b)})?\n\nLocked files currently in use will be skipped.",
            parent=self,
        ):
            return

        self.set_status("Cleaning selected targets...")
        self._start_job(
            lambda: clean_categories(self.clean_categories_data, selected),
            self._on_clean_finished,
        )

    def _on_clean_finished(self, result: Any) -> None:
        if isinstance(result, dict):
            freed = result["freed_bytes"]
            files = result["files_deleted"]
            msg = f"Cleaned {_format_bytes(freed)} across {files} files successfully!"
            self.clean_status_var.set(msg)
            self.set_status(msg)
            messagebox.showinfo("PC Booster Pro", msg, parent=self)
            self._scan_cleaner()

    # =========================================================================
    # PAGE 3: MEMORY MANAGER
    # =========================================================================

    def _render_memory(self) -> None:
        self._section_heading(
            self.page_body,
            self._t("nav_memory"),
            "Review visible desktop applications. Ask selected apps to close normally with save prompts.",
        )

        listing = self._card(self.page_body, padx=14, pady=12)
        listing.pack(fill="both", expand=True)

        header = tk.Frame(listing, bg=SURFACE)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(
            header,
            text="Visible User Applications",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
        ).pack(side="left")
        self._button(header, "Refresh", self._refresh_ram_apps, compact=True).pack(side="right")

        self.ram_summary_var = tk.StringVar(value="Scanning active applications...")
        tk.Label(
            listing,
            textvariable=self.ram_summary_var,
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        columns = ("app", "window", "memory", "pid")
        self.ram_tree = ttk.Treeview(listing, columns=columns, show="headings", selectmode="extended", height=12)
        self.ram_tree.heading("app", text="Application")
        self.ram_tree.heading("window", text="Window Title")
        self.ram_tree.heading("memory", text="Working-Set Memory")
        self.ram_tree.heading("pid", text="PID")
        self.ram_tree.column("app", width=180, anchor="w")
        self.ram_tree.column("window", width=380, anchor="w")
        self.ram_tree.column("memory", width=120, anchor="center", stretch=False)
        self.ram_tree.column("pid", width=80, anchor="center", stretch=False)

        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.ram_tree.yview)
        self.ram_tree.configure(yscrollcommand=scroll.set)
        self.ram_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = tk.Frame(self.page_body, bg=BG)
        actions.pack(fill="x", pady=(10, 0))
        self._button(actions, "Request Graceful Close (WM_CLOSE)", self._close_selected_ram_apps, primary=True).pack(side="right")
        self._button(actions, "Clear Selection", self._clear_ram_selection, compact=True).pack(side="left")

        self._refresh_ram_apps()

    def _refresh_ram_apps(self) -> None:
        if self.busy or self.current_page != "memory":
            return
        self.ram_summary_var.set("Scanning visible applications...")
        self._start_job(ram_manager.list_background_apps, self._ram_apps_loaded)

    def _ram_apps_loaded(self, apps: Any) -> None:
        if not isinstance(apps, list):
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
                values=(app["name"], _short(app["title"], 75), _format_bytes(app["memory"]), app["pid"]),
            )
        self.ram_summary_var.set(f"{len(apps)} eligible desktop apps found. Core Windows processes excluded.")

    def _clear_ram_selection(self) -> None:
        if hasattr(self, "ram_tree"):
            self.ram_tree.selection_remove(self.ram_tree.selection())

    def _close_selected_ram_apps(self) -> None:
        if self.busy or not hasattr(self, "ram_tree"):
            return
        selected = self.ram_tree.selection()
        apps = [self.ram_rows[item] for item in selected if item in self.ram_rows]
        if not apps:
            self.set_status("Select at least one app first.")
            return
        names = "\n".join(f"• {a['name']}" for a in apps[:6])
        if not messagebox.askyesno(
            "Confirm Close Request",
            f"Send normal close request to {len(apps)} app(s)?\n{names}\n\nEach app will display its standard save prompt.",
            parent=self,
        ):
            return
        self._start_job(lambda: ram_manager.request_graceful_close(apps), self._ram_close_finished)

    def _ram_close_finished(self, result: Any) -> None:
        if isinstance(result, dict):
            req = len(result.get("requested", []))
            self.set_status(f"Sent normal close request to {req} app(s).")
            self.after(1200, self._refresh_ram_apps)

    # =========================================================================
    # PAGE 4: STARTUP APPS
    # =========================================================================

    def _render_startup(self) -> None:
        self._section_heading(
            self.page_body,
            self._t("nav_startup"),
            "Manage user startup entries. Disabling an entry prevents auto-launching without uninstalling.",
        )

        active_card = self._card(self.page_body, padx=14, pady=12)
        active_card.pack(fill="both", expand=True, pady=(0, 10))

        head = tk.Frame(active_card, bg=SURFACE)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="Starts with Windows", bg=SURFACE, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")
        self._button(head, "Refresh", self._refresh_startup, compact=True).pack(side="right")

        self.active_startup_tree = ttk.Treeview(active_card, columns=("name", "cmd"), show="headings", height=7)
        self.active_startup_tree.heading("name", text="Application Name")
        self.active_startup_tree.heading("cmd", text="Startup Command")
        self.active_startup_tree.column("name", width=220, anchor="w")
        self.active_startup_tree.column("cmd", width=550, anchor="w")

        sc1 = ttk.Scrollbar(active_card, orient="vertical", command=self.active_startup_tree.yview)
        self.active_startup_tree.configure(yscrollcommand=sc1.set)
        self.active_startup_tree.pack(side="left", fill="both", expand=True)
        sc1.pack(side="right", fill="y")

        act_foot = tk.Frame(active_card, bg=SURFACE)
        act_foot.pack(fill="x", pady=(8, 0))
        self._button(act_foot, "Disable Selected Entry", self._disable_startup_entry, primary=True, compact=True).pack(side="left")

        # Disabled entries card
        dis_card = self._card(self.page_body, padx=14, pady=12)
        dis_card.pack(fill="both", expand=True)

        tk.Label(dis_card, text="Disabled Entries (Can be Restored)", bg=SURFACE, fg=TEXT, font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 6))

        self.disabled_startup_tree = ttk.Treeview(dis_card, columns=("name", "cmd"), show="headings", height=4)
        self.disabled_startup_tree.heading("name", text="Application Name")
        self.disabled_startup_tree.heading("cmd", text="Saved Command")
        self.disabled_startup_tree.column("name", width=220, anchor="w")
        self.disabled_startup_tree.column("cmd", width=550, anchor="w")

        sc2 = ttk.Scrollbar(dis_card, orient="vertical", command=self.disabled_startup_tree.yview)
        self.disabled_startup_tree.configure(yscrollcommand=sc2.set)
        self.disabled_startup_tree.pack(side="left", fill="both", expand=True)
        sc2.pack(side="right", fill="y")

        dis_foot = tk.Frame(dis_card, bg=SURFACE)
        dis_foot.pack(fill="x", pady=(8, 0))
        self._button(dis_foot, "Restore Selected Entry", self._restore_startup_entry, compact=True).pack(side="left")

        self._refresh_startup()

    def _refresh_startup(self) -> None:
        try:
            active = startup.list_active()
            disabled = startup.list_disabled()
        except OSError:
            active, disabled = [], []

        if hasattr(self, "active_startup_tree"):
            self.active_startup_tree.delete(*self.active_startup_tree.get_children())
            self.startup_active_rows = {}
            for idx, item in enumerate(active):
                row_id = f"a_{idx}"
                self.startup_active_rows[row_id] = item["name"]
                self.active_startup_tree.insert("", "end", iid=row_id, values=(item["name"], _short(item["command"])))

        if hasattr(self, "disabled_startup_tree"):
            self.disabled_startup_tree.delete(*self.disabled_startup_tree.get_children())
            self.startup_disabled_rows = {}
            for idx, item in enumerate(disabled):
                row_id = f"d_{idx}"
                self.startup_disabled_rows[row_id] = item["name"]
                self.disabled_startup_tree.insert("", "end", iid=row_id, values=(item["name"], _short(item["command"])))

    def _disable_startup_entry(self) -> None:
        if not hasattr(self, "active_startup_tree"):
            return
        sel = self.active_startup_tree.selection()
        names = [self.startup_active_rows[r] for r in sel if r in self.startup_active_rows]
        if not names:
            self.set_status("Select an entry to disable.")
            return
        for name in names:
            try:
                startup.disable(name)
            except Exception:
                pass
        self._refresh_startup()
        self.set_status(f"Disabled {len(names)} startup entries.")

    def _restore_startup_entry(self) -> None:
        if not hasattr(self, "disabled_startup_tree"):
            return
        sel = self.disabled_startup_tree.selection()
        names = [self.startup_disabled_rows[r] for r in sel if r in self.startup_disabled_rows]
        if not names:
            self.set_status("Select an entry to restore.")
            return
        for name in names:
            try:
                startup.restore(name)
            except Exception:
                pass
        self._refresh_startup()
        self.set_status(f"Restored {len(names)} startup entries.")

    # =========================================================================
    # PAGE 5: REVERSIBLE WINDOWS TWEAKS
    # =========================================================================

    def _render_tweaks(self) -> None:
        self._section_heading(self.page_body, self._t("tweaks_title"), self._t("tweaks_desc"))

        try:
            states = tweaks.current_states()
        except OSError:
            states = {}

        self.tweak_vars = {}
        self.tweak_status_vars = {}
        self.tweak_status_labels = {}

        for key, item in tweaks.TWEAKS.items():
            card = self._card(self.page_body, padx=16, pady=10)
            card.pack(fill="x", pady=4)

            top = tk.Frame(card, bg=SURFACE)
            top.pack(fill="x")

            applied = states.get(key, False)
            var = tk.BooleanVar(value=applied)
            self.tweak_vars[key] = var

            chk = ttk.Checkbutton(top, text=item["label"], variable=var, style="Dark.TCheckbutton")
            chk.pack(side="left")

            st_var = tk.StringVar(value="Applied" if applied else "Not applied")
            self.tweak_status_vars[key] = st_var
            lbl = tk.Label(top, textvariable=st_var, bg=SURFACE, fg=ACCENT if applied else MUTED, font=(FONT, 8, "bold"))
            lbl.pack(side="right")
            self.tweak_status_labels[key] = lbl

            tk.Label(
                card,
                text=item["description"],
                bg=SURFACE,
                fg=MUTED,
                font=(FONT, 8),
                anchor="w",
            ).pack(fill="x", padx=(24, 0), pady=(3, 0))

        actions = tk.Frame(self.page_body, bg=BG)
        actions.pack(fill="x", pady=(12, 0))
        self._button(actions, self._t("apply_tweaks_btn"), self._apply_tweaks, primary=True).pack(side="right")
        self._button(actions, "Apply Suggested Gaming Settings", self._apply_recommended_tweaks, color=ACCENT_CYAN).pack(side="left", padx=(0, 8))
        self._button(actions, self._t("restore_tweaks_btn"), self._restore_tweaks).pack(side="left")

    def _apply_tweaks(self) -> None:
        selected = [k for k, v in self.tweak_vars.items() if v.get()]
        if not selected:
            self.set_status("Select at least one tweak.")
            return
        done = tweaks.apply_selected(selected)
        self._refresh_tweak_status()
        self.set_status(f"Applied {len(done)} tweaks. Original settings backed up.")
        messagebox.showinfo("PC Booster Pro", f"Applied {len(done)} tweaks successfully!\nOriginal settings are backed up locally.", parent=self)

    def _apply_recommended_tweaks(self) -> None:
        for v in self.tweak_vars.values():
            v.set(True)
        self._apply_tweaks()

    def _restore_tweaks(self) -> None:
        restored = tweaks.restore_original()
        self._refresh_tweak_status()
        self.set_status(f"Restored {len(restored)} original settings.")
        messagebox.showinfo("PC Booster Pro", f"Restored {len(restored)} original settings.", parent=self)

    def _refresh_tweak_status(self) -> None:
        try:
            states = tweaks.current_states()
        except OSError:
            states = {}
        for k, v in self.tweak_status_vars.items():
            app = states.get(k, False)
            v.set("Applied" if app else "Not applied")
            if k in self.tweak_status_labels:
                self.tweak_status_labels[k].configure(fg=ACCENT if app else MUTED)

    # =========================================================================
    # PAGE 6: HARDWARE & SYSTEM DIAGNOSTICS (SPECS)
    # =========================================================================

    def _render_specs(self) -> None:
        self._section_heading(self.page_body, self._t("specs_title"), self._t("specs_desc"))

        specs = get_full_system_specs()
        cpu = specs["cpu"]
        mem = specs["memory"]
        os_info = specs["os"]

        # CPU & Memory Cards Row
        row1 = tk.Frame(self.page_body, bg=BG)
        row1.pack(fill="x", pady=(0, 10))
        row1.grid_columnconfigure(0, weight=1, uniform="specs_top")
        row1.grid_columnconfigure(1, weight=1, uniform="specs_top")

        # CPU Card
        cpu_card = self._card(row1, padx=16, pady=12)
        cpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        tk.Label(cpu_card, text="🧠 Processor (CPU)", bg=SURFACE, fg=ACCENT, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(cpu_card, text=cpu["name"], bg=SURFACE, fg=TEXT, font=(FONT, 11, "bold"), wraplength=380).pack(anchor="w", pady=(4, 6))
        tk.Label(
            cpu_card,
            text=f"Cores: {cpu['physical_cores']} Physical • {cpu['logical_cores']} Threads\nClock Speed: ~{cpu['current_ghz']:.2f} GHz",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
        ).pack(anchor="w")

        # Memory Card
        mem_card = self._card(row1, padx=16, pady=12)
        mem_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        tk.Label(mem_card, text="⚡ System Memory (RAM)", bg=SURFACE, fg=ACCENT_CYAN, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(mem_card, text=f"{_format_bytes(mem['total_bytes'])} Total RAM", bg=SURFACE, fg=TEXT, font=(FONT, 13, "bold")).pack(anchor="w", pady=(4, 6))
        tk.Label(
            mem_card,
            text=f"Used: {_format_bytes(mem['used_bytes'])} ({mem['percent']:.1f}%)\nFree: {_format_bytes(mem['free_bytes'])}",
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
        ).pack(anchor="w")

        # GPU Card
        gpu_card = self._card(self.page_body, padx=16, pady=12)
        gpu_card.pack(fill="x", pady=(0, 10))
        tk.Label(gpu_card, text="🎮 Graphics Accelerators (GPU)", bg=SURFACE, fg=WARN, font=(FONT, 11, "bold")).pack(anchor="w")
        for gpu_name in specs["gpus"]:
            tk.Label(gpu_card, text=f"• {gpu_name}", bg=SURFACE, fg=TEXT, font=(FONT, 10, "bold")).pack(anchor="w", pady=(2, 0))

        # Storage Partitions Deck
        drives_card = self._card(self.page_body, padx=16, pady=12)
        drives_card.pack(fill="x", pady=(0, 10))
        tk.Label(drives_card, text="💾 Storage Volumes & Partitions", bg=SURFACE, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 8))

        for drive in specs["drives"]:
            d_row = tk.Frame(drives_card, bg=SURFACE)
            d_row.pack(fill="x", pady=4)
            tk.Label(
                d_row,
                text=f"{drive['mount']} ({drive['fstype']}) - Free {_format_bytes(drive['free'])} of {_format_bytes(drive['total'])}",
                bg=SURFACE,
                fg=TEXT,
                font=(FONT, 9, "bold"),
            ).pack(side="left")
            pct_label = tk.Label(d_row, text=f"{drive['percent']:.0f}% Used", bg=SURFACE, fg=MUTED, font=(FONT, 8))
            pct_label.pack(side="right")

            # Progress bar
            pbar = ttk.Progressbar(drives_card, mode="determinate", maximum=100, value=drive["percent"], style="Green.Horizontal.TProgressbar")
            pbar.pack(fill="x", pady=(1, 6))

        # Windows OS & Uptime Card
        os_card = self._card(self.page_body, padx=16, pady=12)
        os_card.pack(fill="x")
        tk.Label(os_card, text="🪟 Windows OS & Host Environment", bg=SURFACE, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(
            os_card,
            text=(
                f"Operating System: {os_info['product']} {os_info['version_tag']}\n"
                f"Architecture: {os_info['architecture']} • Computer Name: {os_info['hostname']}\n"
                f"System Uptime: {os_info['uptime']} (Since last reboot)"
            ),
            bg=SURFACE,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    # =========================================================================
    # PAGE 7: EXTENDED SETTINGS HUB & PROCESS WHITELIST
    # =========================================================================

    def _render_settings(self) -> None:
        self._section_heading(self.page_body, self._t("settings_title"), self._t("settings_desc"))

        # Section 1: Auto-Pilot Sentinel Engine Rules
        s_card = self._card(self.page_body, padx=16, pady=12)
        s_card.pack(fill="x", pady=(0, 10))
        tk.Label(s_card, text="🤖 Auto-Pilot Sentinel Rules", bg=SURFACE, fg=ACCENT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 8))

        self.set_ap_enabled = tk.BooleanVar(value=self.settings.get("auto_pilot_enabled", True))
        ttk.Checkbutton(s_card, text="Enable Autonomous Auto-Pilot Sentinel (Zero-Kill Background Sentry)", variable=self.set_ap_enabled, style="Dark.TCheckbutton").pack(anchor="w")

        self.set_turbo_enabled = tk.BooleanVar(value=self.settings.get("foreground_turbo", True))
        ttk.Checkbutton(s_card, text="Foreground Turbo Shield (Elevate active game/software priority)", variable=self.set_turbo_enabled, style="Dark.TCheckbutton").pack(anchor="w", pady=(4, 0))

        self.set_taming_enabled = tk.BooleanVar(value=self.settings.get("background_ram_taming", True))
        ttk.Checkbutton(s_card, text="Background Bloatware Tamer (Silent working-set trim for idle apps >350 MB)", variable=self.set_taming_enabled, style="Dark.TCheckbutton").pack(anchor="w", pady=(4, 0))

        # Section 2: Process Immunity Whitelist
        w_card = self._card(self.page_body, padx=16, pady=12)
        w_card.pack(fill="x", pady=(0, 10))
        tk.Label(w_card, text=f"🛡️ {self._t('whitelist_title')}", bg=SURFACE, fg=ACCENT_CYAN, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(w_card, text=self._t("whitelist_desc"), bg=SURFACE, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(2, 8))

        w_input_row = tk.Frame(w_card, bg=SURFACE)
        w_input_row.pack(fill="x", pady=(0, 6))

        self.whitelist_input = tk.Entry(w_input_row, bg=SURFACE_ALT, fg=TEXT, insertbackground=ACCENT, relief="flat", font=(FONT, 9))
        self.whitelist_input.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=5)
        self.whitelist_input.insert(0, "app.exe")

        self._button(w_input_row, f"+ {self._t('add_app_btn')}", self._add_whitelist_app, primary=True, compact=True).pack(side="right")
        self._button(w_input_row, f"- {self._t('remove_app_btn')}", self._remove_whitelist_app, compact=True).pack(side="right", padx=(0, 6))

        self.whitelist_box = tk.Listbox(
            w_card,
            bg=BG,
            fg=TEXT,
            selectbackground=ACCENT_DARK,
            selectforeground=TEXT,
            relief="flat",
            height=6,
            font=(FONT, 9),
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.whitelist_box.pack(fill="x")
        for item in self.settings.get("whitelist", []):
            self.whitelist_box.insert("end", item)

        # Section 3: System & Tray Settings
        tray_card = self._card(self.page_body, padx=16, pady=12)
        tray_card.pack(fill="x", pady=(0, 10))
        tk.Label(tray_card, text="🖥️ System & Tray Behavior", bg=SURFACE, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 8))

        try:
            start_login = autostart.is_enabled()
        except OSError:
            start_login = False

        self.set_start_login = tk.BooleanVar(value=start_login)
        ttk.Checkbutton(tray_card, text="Start PC Booster Pro when I sign in to Windows", variable=self.set_start_login, style="Dark.TCheckbutton").pack(anchor="w")

        self.set_close_tray = tk.BooleanVar(value=self.settings.get("close_to_tray", True))
        ttk.Checkbutton(tray_card, text="Keep running in system tray when window is closed or minimized", variable=self.set_close_tray, style="Dark.TCheckbutton").pack(anchor="w", pady=(4, 0))

        # Save Button
        save_foot = tk.Frame(self.page_body, bg=BG)
        save_foot.pack(fill="x", pady=(8, 0))
        self._button(save_foot, f"💾 {self._t('save_settings_btn')}", self._save_all_settings, primary=True).pack(side="right")

    def _add_whitelist_app(self) -> None:
        val = self.whitelist_input.get().strip().lower()
        if not val or not val.endswith(".exe"):
            messagebox.showwarning("PC Booster Pro", "Please enter a valid executable name (e.g. game.exe)", parent=self)
            return
        wl = self.settings.get("whitelist", [])
        if val not in wl:
            wl.append(val)
            self.settings["whitelist"] = wl
            self.whitelist_box.insert("end", val)
            self.sentinel.update_settings(self.settings)
            save_settings(self.settings)

    def _remove_whitelist_app(self) -> None:
        sel = self.whitelist_box.curselection()
        if not sel:
            return
        idx = sel[0]
        val = self.whitelist_box.get(idx)
        wl = self.settings.get("whitelist", [])
        if val in wl:
            wl.remove(val)
            self.settings["whitelist"] = wl
            self.whitelist_box.delete(idx)
            self.sentinel.update_settings(self.settings)
            save_settings(self.settings)

    def _save_all_settings(self) -> None:
        self.settings["auto_pilot_enabled"] = self.set_ap_enabled.get()
        self.settings["foreground_turbo"] = self.set_turbo_enabled.get()
        self.settings["background_ram_taming"] = self.set_taming_enabled.get()
        self.settings["close_to_tray"] = self.set_close_tray.get()

        # Update Windows startup
        login_val = self.set_start_login.get()
        try:
            autostart.set_enabled(login_val)
        except Exception:
            pass

        save_settings(self.settings)
        self.sentinel.update_settings(self.settings)
        self.set_status("All extended settings saved successfully.")
        messagebox.showinfo("PC Booster Pro", "Extended settings saved successfully!", parent=self)

    # =========================================================================
    # PERIODIC MONITORING & SYSTEM TRAY
    # =========================================================================

    def _update_home(self, data: dict[str, Any]) -> None:
        if self.current_page != "home":
            return

        # 1. Update Circular Gauges
        if "cpu" in self.gauges:
            self.gauges["cpu"].set_value(data["cpu"], f"{data['cpu']:.1f}% capacity")
        if "ram" in self.gauges:
            self.gauges["ram"].set_value(
                data["memory_percent"],
                f"Free {_format_bytes(data['memory_available'])}",
            )
        if "disk" in self.gauges:
            disk = data.get("disk")
            if disk:
                self.gauges["disk"].set_value(
                    disk.percent,
                    f"Free {_format_bytes(disk.free)}",
                )
        if "gpu" in self.gauges:
            gpu = data.get("gpu")
            if gpu:
                detail = f"{gpu['temperature']:.0f}°C" if "temperature" in gpu else "Engine Active"
                self.gauges["gpu"].set_value(gpu["usage"], detail)

        # 2. Update Sentinel Stat Chips
        stats = self.sentinel.get_stats()
        if hasattr(self, "chip_turbo") and self.chip_turbo.winfo_exists():
            self.chip_turbo.configure(text=stats["active_boosted_app"])
            self.chip_tamed.configure(text=f"{stats['tamed_count']} instances")
            self.chip_freed.configure(text=_format_bytes(stats["total_freed_bytes"]))
            self._update_event_hud()

        # 3. Update Process Table
        if hasattr(self, "process_tree") and self.process_tree.winfo_exists():
            self.process_tree.delete(*self.process_tree.get_children())
            for p in data["processes"][:8]:
                self.process_tree.insert(
                    "",
                    "end",
                    values=(p["name"], f"{p['cpu']:.1f}%", _format_bytes(p["memory"])),
                )

    def _open_task_manager(self) -> None:
        try:
            subprocess.Popen(["taskmgr.exe"], close_fds=True)
        except OSError:
            pass

    def _ensure_tray(self) -> bool:
        if self.tray_icon is not None:
            return True
        try:
            import pystray
            from PIL import Image

            with Image.open(_asset_path("pc_booster.ico")) as source:
                tray_image = source.convert("RGBA").copy()

            menu = pystray.Menu(
                pystray.MenuItem("⚡ PC Booster Pro", None, enabled=False),
                pystray.MenuItem("🚀 Quick Boost Now", self._tray_quick_boost),
                pystray.MenuItem("🛡️ Open Dashboard", self._tray_open, default=True),
                pystray.MenuItem("⚙️ Settings & Whitelist", self._tray_settings),
                pystray.MenuItem("❌ Exit PC Booster", self._tray_exit),
            )
            self.tray_icon = pystray.Icon("pc_booster", tray_image, "PC Booster Pro", menu)
            self.tray_icon.run_detached()
            return True
        except Exception:
            self.tray_icon = None
            return False

    def _tray_quick_boost(self, _icon: Any, _item: Any) -> None:
        self.after(0, self._trigger_quick_boost)

    def _tray_open(self, _icon: Any, _item: Any) -> None:
        self.after(0, self._restore_from_tray)

    def _tray_settings(self, _icon: Any, _item: Any) -> None:
        self.after(0, lambda: (self._restore_from_tray(), self.show_page("settings")))

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

    def _on_unmap(self, _event: tk.Event) -> None:
        if self.settings.get("close_to_tray", True) and self.state() == "iconic":
            self.after_idle(self._hide_minimized_window)

    def _hide_minimized_window(self) -> None:
        if self.state() == "iconic" and self._ensure_tray():
            self.withdraw()

    def _request_close(self) -> None:
        if not self.settings.get("close_to_tray", True):
            self._close()
            return
        if self._ensure_tray():
            self.withdraw()
            return
        self._close()

    def _tick(self) -> None:
        if self.closing:
            return
        if not self.monitor_busy:
            self.monitor_busy = True

            def sample() -> None:
                try:
                    self.monitor_pending.put((self.monitor.sample(), None))
                except Exception as err:
                    self.monitor_pending.put((None, err))

            threading.Thread(target=sample, name="MonitorSampleThread", daemon=True).start()

        interval = 8000 if self.state() == "withdrawn" else 2500
        self.after(interval, self._tick)

    def _poll_pending(self) -> None:
        try:
            while True:
                cb, res, err = self.pending.get_nowait()
                self.busy = False
                if err is not None:
                    self.set_status("Operation failed.")
                    messagebox.showerror("PC Booster Pro", str(err), parent=self)
                else:
                    cb(res)
        except queue.Empty:
            pass

        try:
            while True:
                res, err = self.monitor_pending.get_nowait()
                self.monitor_busy = False
                if res is not None:
                    self.last_snapshot = res
                    self._update_home(res)
        except queue.Empty:
            pass

        if not self.closing:
            self.after(120, self._poll_pending)

    def _start_job(self, operation: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        self.busy = True

        def run() -> None:
            try:
                self.pending.put((callback, operation(), None))
            except Exception as err:
                self.pending.put((callback, None, err))

        threading.Thread(target=run, name="WorkerJobThread", daemon=True).start()

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.sentinel.stop()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.monitor.close()
        self.destroy()
