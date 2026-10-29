"""Tkinter GUI for the GSA SEO Indexer clone.

The layout mirrors the original: a single-URL entry field with a toolbar on
the right, a URL list, a progress bar and the live ``Q:`` / ``LpM:`` counters.
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from . import engine
from .engine import (
    Indexer,
    PingTarget,
    Settings,
    default_targets,
    load_modern_targets,
    load_ping_sites,
    read_url_file,
    validate_url,
)
from .paths import resource_dir, runtime_dir

HERE = runtime_dir()
REPORT_DIR = HERE / "reports"
CONFIG_PATH = HERE / "config.ini"
SERVICE_DIR = resource_dir() / "index_service"
QUEUE_PATH = HERE / "last_queue.txt"

# Light/dark palettes.  Tag colors differ per theme so the Link column
# stays readable on both.
THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#f0f0f0",
        "fg": "#1a1a1a",
        "field": "#ffffff",
        "fieldbg": "#ffffff",
        "border": "#c9c9c9",
        "selectbg": "#cde8ff",
        "selectfg": "#1a1a1a",
        "disabled": "#8a8a8a",
        "accent": "#555555",
        "tags": {
            "indexed": "#0b6b0b",
            "listed": "#8a5a00",
            "echo": "#6e6e6e",
            "failed": "#b02020",
            "skipped": "#6e6e6e",
        },
    },
    "dark": {
        "bg": "#1e1e1e",
        "fg": "#e8e8e8",
        "field": "#2a2a2a",
        "fieldbg": "#252526",
        "border": "#454545",
        "selectbg": "#264f78",
        "selectfg": "#ffffff",
        "disabled": "#7a7a7a",
        "accent": "#bbbbbb",
        "tags": {
            "indexed": "#4ec94e",
            "listed": "#d7a54a",
            "echo": "#9a9a9a",
            "failed": "#e06c6c",
            "skipped": "#9a9a9a",
        },
    },
}


class IndexerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{engine.APP_NAME} v{engine.APP_VERSION}")
        self.geometry("780x520")
        self.minsize(700, 420)

        # window/taskbar icon; the .exe file icon itself comes from the spec
        ico = resource_dir() / "assets" / "app.ico"
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except tk.TclError:
                pass

        # config.ini is shared with the CLI: whatever was saved last time
        # (threads, proxies, target list, ...) comes back on launch.
        self.settings = Settings.load(CONFIG_PATH)
        self.indexer = Indexer(self.settings)
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._validation_skipped = 0
        self._backlinks = 0
        # True while an index-service submission (not a ping run) holds the
        # worker, so _poll does not overwrite its progress with engine stats.
        self._svc_run = False
        self._svc_progress = 0

        self.var_url = tk.StringVar()
        self.var_status = tk.StringVar(value="Ready")
        self.var_queue = tk.StringVar(value="Q: 0")
        self.var_lpm = tk.StringVar(value="LpM: 0")
        self.var_backlinks = tk.StringVar(value="Backlinks: 0")
        self.var_validated = tk.StringVar(value="")
        self.var_threads = tk.IntVar(value=self.settings.threads)
        self.var_check = tk.BooleanVar(value=self.settings.check_url)
        self.var_stats_only = tk.BooleanVar(value=self.settings.statistics_only)
        self.var_custom = tk.BooleanVar(value=self.settings.custom_mode)
        self.var_custom_number = tk.IntVar(value=self.settings.custom_mode_number)
        self.var_deep_link = tk.BooleanVar(value=self.settings.deep_link_only)
        self.var_min_tray = tk.BooleanVar(value=self.settings.min_to_tray)
        self._tray = None
        self._tray_minimized = False
        self.var_site_filter = tk.StringVar(value=self.settings.site_filter)
        self.var_use_proxy = tk.BooleanVar(value=self.settings.use_proxy)
        self.var_proxy = tk.StringVar(value=", ".join(self.settings.proxy_list))
        self.var_batch = tk.StringVar(value=str(self.settings.batch_size))
        self.var_fast = tk.BooleanVar(value=self.settings.fast_mode)
        self.var_repair = tk.BooleanVar(value=self.settings.repair_misses)
        self.var_target_list = tk.StringVar(value=self.settings.target_list)
        self.var_dark = tk.BooleanVar(value=self.settings.theme == "dark")
        # results-table filter state
        self.var_search = tk.StringVar(value="")
        self.var_filter = tk.StringVar(value="All")
        self.var_shown = tk.StringVar(value="0 rows")
        self._all_rows: list[str] = []      # iids in display (sorted) order
        self._row_tag: dict[str, str] = {}  # iid -> row tag
        self._sort_desc: dict[str, bool] = {}

        self._setup_style()
        self._build()
        self._apply_tag_colors()
        self.after(100, self._poll)
        self.after(200, self._maybe_resume_queue)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        """Apply the light or dark palette to every widget class.

        The default 'vista' theme ignores background configuration, so the
        app is switched to 'clam' which honours it; on the dark palette the
        built-in theme would leave white patches everywhere.
        """
        theme = "dark" if self.var_dark.get() else "light"
        p = THEMES[theme]
        self._palette = p
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(background=p["bg"])
        self.option_add("*Background", p["bg"])
        self.option_add("*Foreground", p["fg"])
        self.option_add("*Font", ("Segoe UI", 10))

        style.configure(".", background=p["bg"], foreground=p["fg"],
                        bordercolor=p["border"], fieldbackground=p["fieldbg"])
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        style.configure("TRadiobutton", background=p["bg"], foreground=p["fg"])
        style.configure("TButton", background=p["field"], foreground=p["fg"],
                        bordercolor=p["border"])
        style.map("TButton",
                  background=[("active", p["selectbg"])],
                  foreground=[("disabled", p["disabled"])])
        style.configure("TEntry", fieldbackground=p["fieldbg"], foreground=p["fg"],
                        bordercolor=p["border"])
        style.configure("TSpinbox", fieldbackground=p["fieldbg"], foreground=p["fg"],
                        arrowcolor=p["fg"], bordercolor=p["border"])
        style.configure("TCombobox", fieldbackground=p["fieldbg"], foreground=p["fg"],
                        background=p["field"], arrowcolor=p["fg"], bordercolor=p["border"])
        style.configure("TProgressbar", background="#2f7dd1", troughcolor=p["fieldbg"],
                        bordercolor=p["border"], lightcolor="#2f7dd1", darkcolor="#2f7dd1")
        style.configure("Treeview", background=p["fieldbg"], foreground=p["fg"],
                        fieldbackground=p["fieldbg"], bordercolor=p["border"])
        # row height follows the display DPI so text does not clip when the
        # process is DPI-aware (24 px is the 100%-scaling baseline)
        try:
            row_h = max(24, round(24 * self.winfo_fpixels("1i") / 96))
        except (tk.TclError, ZeroDivisionError):
            row_h = 24
        style.configure("Treeview", rowheight=row_h)
        style.configure("Treeview.Heading", background=p["field"], foreground=p["fg"],
                        bordercolor=p["border"], relief="flat")
        style.configure("Hint.TLabel", background=p["bg"], foreground=p["disabled"])
        style.map("Treeview", background=[("selected", p["selectbg"])],
                  foreground=[("selected", p["selectfg"])])
        style.map("Treeview.Heading", background=[("active", p["selectbg"])])
        if hasattr(self, "tree"):
            self._apply_tag_colors()

    def _apply_tag_colors(self) -> None:
        for tag, color in self._palette["tags"].items():
            self.tree.tag_configure(tag, foreground=color)

    # ------------------------------------------------------------------
    # Minimize to tray (the original's "min to tray" config key)
    # ------------------------------------------------------------------
    def _toggle_min_tray(self) -> None:
        self.settings.min_to_tray = self.var_min_tray.get()
        try:
            self.settings.save(CONFIG_PATH)
        except OSError:
            pass

    def _on_unmap(self, event) -> None:
        """A real minimise (state iconic) hides to the tray when enabled."""
        if event.widget is not self:
            return
        if self.var_min_tray.get() and self.state() == "iconic":
            self._minimize_to_tray()

    def _minimize_to_tray(self) -> None:
        if self._tray is None:
            try:
                from .tray import TrayIcon

                self._tray = TrayIcon(
                    on_activate=self._restore_from_tray, on_quit=self._quit
                )
            except (ImportError, OSError):
                # no tray on this platform/host - minimise normally
                self._tray = None
                return
        if self._tray.show():
            self.withdraw()
            self._tray_minimized = True

    def _restore_from_tray(self) -> None:
        if self._tray is not None:
            self._tray.hide()
        self._tray_minimized = False
        self.deiconify()
        self.lift()
        self.focus_force()

    def _toggle_theme(self) -> None:
        dark = self.var_dark.get()
        self.settings.theme = "dark" if dark else "light"
        self._setup_style()
        try:
            self.settings.save(CONFIG_PATH)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build(self) -> None:
        # URL entry row
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="URL/Domain:").pack(side="left")
        self.entry = ttk.Entry(toolbar, textvariable=self.var_url)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda _e: self._start_single())

        self.btn_start = ttk.Button(toolbar, text="Start", command=self._start_single)
        self.btn_start.pack(side="left", padx=2)
        ttk.Button(toolbar, text="Multiple", command=self._open_queue).pack(side="left", padx=2)
        self.btn_stop = ttk.Button(toolbar, text="Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=2)

        # Options area: grouped LabelFrames in two rows so the controls fit
        # inside the default 780px window instead of overflowing 566px.
        opts_outer = ttk.Frame(self, padding=(8, 0, 8, 4))
        opts_outer.pack(fill="x")

        # Row 1: Run options
        run_frame = ttk.LabelFrame(opts_outer, text="Run", padding=(8, 4))
        run_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(run_frame, text="Threads:").pack(side="left")
        ttk.Spinbox(
            run_frame, from_=1, to=200, textvariable=self.var_threads, width=6
        ).pack(side="left", padx=(4, 12))
        w = ttk.Checkbutton(run_frame, text="Check URL first", variable=self.var_check)
        w.pack(side="left")
        self._tip(w, "Verify the target answers before spending requests on it.")
        w = ttk.Checkbutton(run_frame, text="Statistics only", variable=self.var_stats_only)
        w.pack(side="left", padx=8)
        self._tip(w, "Submit only to the authority statistics hosts - much faster, fewer real links.")
        w = ttk.Checkbutton(run_frame, text="Custom", variable=self.var_custom)
        w.pack(side="left", padx=(6, 2))
        self._tip(w, "Limit the ping list to its first N sites (the original's custom mode).")
        ttk.Spinbox(
            run_frame, from_=1, to=9999, textvariable=self.var_custom_number, width=5
        ).pack(side="left", padx=(0, 6))
        w = ttk.Checkbutton(run_frame, text="Fast (1 req/host)", variable=self.var_fast)
        w.pack(side="left", padx=8)
        self._tip(w, "One request per host (query form) instead of the query+append pair.")
        w = ttk.Checkbutton(run_frame, text="Repair misses", variable=self.var_repair)
        w.pack(side="left", padx=8)
        self._tip(w, "Re-submit targets that failed in the first pass.")

        # Row 2: Sources | Proxies | Batch side by side
        sources_row = ttk.Frame(opts_outer)
        sources_row.pack(fill="x")

        sources_frame = ttk.LabelFrame(sources_row, text="Sources", padding=(8, 4))
        sources_frame.pack(side="left", fill="x", expand=True, padx=(0, 4))
        sel = ttk.Frame(sources_frame)
        sel.pack(fill="x", pady=(0, 3))
        ttk.Label(sel, text="Targets:").pack(side="left")
        for mode, label in (("legacy", "Legacy"), ("modern", "Modern"), ("both", "Both")):
            ttk.Radiobutton(
                sel, text=label, value=mode, variable=self.var_target_list
            ).pack(side="left", padx=(6, 0))
        filt = ttk.Frame(sources_frame)
        filt.pack(fill="x")
        ttk.Label(filt, text="Filter host:").pack(side="left")
        # explicit width keeps the frame's *minimum* small now that the
        # deep-links checkbox shares this row; expand=True still grows it
        ent = ttk.Entry(filt, textvariable=self.var_site_filter, width=14)
        ent.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._tip(ent, "Keep only ping sites whose URL contains this text.")
        # "deep link indexer only" in the original's config; the elastic
        # filter entry yields the space, so the frame's minimum barely moves
        w = ttk.Checkbutton(filt, text="Deep links only", variable=self.var_deep_link)
        w.pack(side="left", padx=(6, 0))
        self._tip(w, "Only sites that index inner pages (drops the legacy ping list).")

        proxy_frame = ttk.LabelFrame(sources_row, text="Proxies", padding=(8, 4))
        proxy_frame.pack(side="left", fill="x", expand=True, padx=(4, 4))
        w = ttk.Checkbutton(proxy_frame, text="Use", variable=self.var_use_proxy)
        w.pack(side="left")
        self._tip(w, "Send requests through the proxies in this field.")
        # explicit width keeps this frame's *minimum* small; expand=True
        # still grows it whenever the window has room
        ttk.Entry(proxy_frame, textvariable=self.var_proxy, width=10).pack(
            side="left", fill="x", expand=True, padx=(4, 4)
        )
        # auto-sized ttk buttons measure a uniform 82 px on this theme, so
        # tight explicit widths are what keep Sources|Proxies|Batch <= 780
        w = ttk.Button(proxy_frame, text="Scrape", command=self._scrape_proxies, width=6)
        w.pack(side="left", padx=1)
        self._tip(w, "Collect fresh public proxies and keep the ones that answer.")
        w = ttk.Button(proxy_frame, text="Load...", command=self._load_proxy_file, width=5)
        w.pack(side="left", padx=1)
        self._tip(w, "Load a proxy list from a file (one per line, # comments allowed).")
        w = ttk.Button(proxy_frame, text="Test", command=self._test_proxies, width=5)
        w.pack(side="left", padx=1)
        self._tip(w, "Test the proxies in the field now; only working ones stay.")

        batch_frame = ttk.LabelFrame(sources_row, text="Batch", padding=(8, 4))
        batch_frame.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Label(batch_frame, text="URLs/request:").pack(side="left")
        ent = ttk.Entry(batch_frame, textvariable=self.var_batch, width=5)
        ent.pack(side="left", padx=(4, 0))
        self._batch_entry = ent
        ent.bind("<FocusOut>", self._validate_batch)
        self._tip(ent, "URLs packed into one request (0 = engine default).")

        # Progress bar + validated line
        progress_frame = ttk.Frame(self, padding=(8, 0, 8, 0))
        progress_frame.pack(fill="x")
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_frame, textvariable=self.var_validated, style="Hint.TLabel").pack(
            side="right", padx=(8, 0)
        )

        # Results filter bar: category + free-text search over every column.
        filter_bar = ttk.Frame(self, padding=(8, 2, 8, 2))
        filter_bar.pack(fill="x")
        ttk.Label(filter_bar, text="Show:").pack(side="left")
        self.filter_box = ttk.Combobox(
            filter_bar,
            textvariable=self.var_filter,
            state="readonly",
            width=11,
            values=("All", "Backlinks", "Listed", "Echo", "Failed", "Skipped"),
        )
        self.filter_box.pack(side="left", padx=(4, 10))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_result_filter())
        self.search_entry = ttk.Entry(filter_bar, textvariable=self.var_search, width=28)
        self.search_entry.pack(side="left")
        self._tip(self.search_entry, "Filter rows by any column (Ctrl+F).")
        # a write trace fires on typing, paste, and programmatic changes alike
        self.var_search.trace_add("write", lambda *_a: self._apply_result_filter())
        ttk.Label(filter_bar, text="Search Target/Site/Link…", style="Hint.TLabel").pack(
            side="left", padx=(4, 0)
        )
        ttk.Label(filter_bar, textvariable=self.var_shown).pack(side="right")
        btn = ttk.Button(filter_bar, text="Clear", width=6, command=self._clear_filter)
        btn.pack(side="right", padx=6)
        self._tip(btn, "Reset the result filter.")

        # Results tree: Target | Host | Link | Code | ms
        list_frame = ttk.Frame(self, padding=(8, 4, 8, 4))
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("target", "host", "link", "code", "ms"),
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("target", text="Target URL")
        self.tree.heading("host", text="Site")
        self.tree.heading("link", text="Link")
        self.tree.heading("code", text="Code")
        self.tree.heading("ms", text="ms")
        self.tree.column("target", width=300, stretch=True)
        self.tree.column("host", width=180)
        self.tree.column("link", width=110, anchor="center")
        self.tree.column("code", width=60, anchor="e")
        self.tree.column("ms", width=70, anchor="e")
        # Color tags for the Link column (reconfigured per theme in _setup_style)
        for tag in ("indexed", "listed", "echo", "failed", "skipped"):
            self.tree.tag_configure(tag)
        for col in ("target", "host", "link", "code", "ms"):
            self.tree.heading(col, command=lambda _c=col: self._sort_results(_c))
        self.tree.bind("<Button-3>", self._show_result_menu)
        self.tree.bind("<Control-c>", lambda _e: self._copy_selected_rows())
        sc = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        hsc = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sc.set, xscrollcommand=hsc.set)
        sc.pack(side="right", fill="y")
        hsc.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        # long target URLs clip at column width; the hover row shows the
        # full line, the bar above scrolls when the window is narrower
        # than the columns need
        self._bind_row_tooltip()

        # Status bar
        bottom = ttk.Frame(self, padding=(8, 4, 8, 8))
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.var_status).pack(side="left")
        ttk.Label(bottom, textvariable=self.var_backlinks).pack(side="right", padx=(10, 10))
        ttk.Label(bottom, textvariable=self.var_lpm).pack(side="right", padx=(10, 0))
        ttk.Label(bottom, textvariable=self.var_queue).pack(side="right")

        menu = tk.Menu(self)
        m_file = tk.Menu(menu, tearoff=0)
        m_file.add_command(label="Import from file...", command=self._import_file)
        m_file.add_command(
            label="Import from clipboard", accelerator="Ctrl+Shift+V", command=self._import_clipboard
        )
        m_file.add_command(label="Export report...", command=self._export_report)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self._quit)
        menu.add_cascade(label="File", menu=m_file)
        m_opts = tk.Menu(menu, tearoff=0)
        m_opts.add_command(label="Advanced settings...", command=self._advanced_settings)
        m_opts.add_checkbutton(label="Dark mode", variable=self.var_dark, command=self._toggle_theme)
        m_opts.add_checkbutton(
            label="Minimize to tray", variable=self.var_min_tray, command=self._toggle_min_tray
        )
        m_opts.add_separator()
        m_opts.add_command(label="Statistics services...", command=self._show_statistics)
        m_opts.add_command(label="Indexing services...", command=self._show_services)
        m_opts.add_command(label="Site list...", command=self._show_sites)
        m_opts.add_command(label="Start index-service emulator...", command=self._start_emulator)
        menu.add_cascade(label="Options", menu=m_opts)
        m_help = tk.Menu(menu, tearoff=0)
        m_help.add_command(label="About...", command=self._show_about)
        menu.add_cascade(label="Help", menu=m_help)
        self.config(menu=menu)

        # Keyboard shortcuts
        self.bind_all("<Control-o>", lambda _e: self._open_queue())
        self.bind_all("<Control-O>", lambda _e: self._open_queue())
        self.bind("<Unmap>", self._on_unmap)
        self.bind_all("<Control-s>", lambda _e: self._export_report())
        self.bind_all("<Control-S>", lambda _e: self._export_report())
        self.bind_all("<Escape>", lambda _e: self._stop())
        self.bind_all("<Control-Shift-V>", lambda _e: self._import_clipboard())
        self.bind_all("<Control-f>", lambda _e: self._focus_search())
        self.bind_all("<Control-F>", lambda _e: self._focus_search())

    def _focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    # ------------------------------------------------------------------
    # Results filter
    # ------------------------------------------------------------------
    FILTER_OF_TAG = {
        "indexed": "Backlinks",
        "listed": "Listed",
        "echo": "Echo",
        "failed": "Failed",
        "skipped": "Skipped",
    }

    SORT_LABELS = {"target": "Target URL", "host": "Site", "link": "Link",
                   "code": "Code", "ms": "ms"}
    LINK_RANK = {"indexed": 0, "listed": 1, "echo": 2, "failed": 3, "skipped": 4}

    def _tip(self, widget: tk.Widget, text: str) -> None:
        """Bind a lightweight tooltip to a widget, colored at show time."""
        state: dict = {"win": None}

        def show(_event) -> None:
            if state["win"] is not None or not text:
                return
            p = self._palette
            win = tk.Toplevel(self)
            win.wm_overrideredirect(True)
            tk.Label(win, text=text, justify="left", padding=(8, 4),
                     background=p["accent"], foreground=p["bg"]).pack()
            win.update_idletasks()
            x = min(widget.winfo_rootx() + 10,
                    win.winfo_screenwidth() - win.winfo_reqwidth() - 8)
            y = widget.winfo_rooty() + widget.winfo_height() + 8
            win.wm_geometry(f"+{x}+{y}")
            state["win"] = win

        def hide(_event=None) -> None:
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")

    def _sort_results(self, col: str) -> None:
        """Order the result rows by a column; click the same column again
        to reverse.

        ``_all_rows`` is the display order, so sorting just reorders that
        list and ``_apply_result_filter`` reattaches everything in one
        pass.  Rows arriving later are appended, not merged into the sort.
        """
        # an unclicked column counts as "descending" so the first click sorts ascending
        new_desc = not self._sort_desc.get(col, True)
        self._sort_desc = {col: new_desc}
        if col in ("code", "ms"):
            def key(iid: str):
                try:
                    return int(self.tree.set(iid, col))
                except (ValueError, TypeError, tk.TclError):
                    return -1
        elif col == "link":
            def key(iid: str):
                return self.LINK_RANK.get(self._row_tag.get(iid, ""), 9)
        else:
            def key(iid: str):
                return str(self.tree.set(iid, col)).lower()
        self._all_rows.sort(key=key, reverse=new_desc)
        for c, label in self.SORT_LABELS.items():
            arrow = ""
            if c in self._sort_desc:
                arrow = "  ▼" if self._sort_desc[c] else "  ▲"
            self.tree.heading(c, text=label + arrow)
        self._apply_result_filter()

    def _show_result_menu(self, event) -> None:
        """Context menu for result rows: copy without a full export."""
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy row(s) as TSV", command=self._copy_selected_rows)
        menu.add_command(
            label="Copy target URL(s)",
            command=lambda: self._copy_selected_rows(column="target"),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_selected_rows(self, column: str = "") -> str:
        """Copy the selected rows (whole line as TSV, or one column)."""
        rows = [self.tree.set(iid) for iid in self.tree.selection()]
        if rows:
            if column:
                lines = [str(r.get(column, "")) for r in rows]
            else:
                lines = ["\t".join(str(v) for v in r.values()) for r in rows]
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
            self.var_status.set(f"Copied {len(lines)} row(s) to the clipboard")
        return "break"

    def _bind_row_tooltip(self) -> None:
        """Hover a result row to read it in full (theme-colored bubble)."""
        state: dict = {"iid": None, "win": None, "after": None}

        def hide() -> None:
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None
            if state["after"] is not None:
                self.after_cancel(state["after"])
                state["after"] = None

        def show(iid: str) -> None:
            try:
                vals = self.tree.set(iid)
            except tk.TclError:
                return
            text = "\n".join(
                str(vals.get(col, "")) for col in ("target", "host", "link") if vals.get(col, "")
            )
            if not text:
                return
            p = self._palette
            win = tk.Toplevel(self)
            win.wm_overrideredirect(True)
            tk.Label(win, text=text, justify="left", padding=(8, 4),
                     background=p["accent"], foreground=p["bg"]).pack()
            win.update_idletasks()
            x, y = self.winfo_pointerxy()
            x = min(x + 14, win.winfo_screenwidth() - win.winfo_reqwidth() - 8)
            win.wm_geometry(f"+{x}+{y + 16}")
            state["win"] = win

        def on_motion(event) -> None:
            iid = self.tree.identify_row(event.y)
            if iid != state["iid"]:
                hide()
                state["iid"] = iid
                if iid:
                    state["after"] = self.after(600, lambda: show(iid))

        def on_leave(_event) -> None:
            hide()
            state["iid"] = None

        self.tree.bind("<Motion>", on_motion, add="+")
        self.tree.bind("<Leave>", on_leave, add="+")
        # scrolling moves rows under a stationary cursor; drop the bubble
        self.tree.bind("<MouseWheel>", lambda _e: hide(), add="+")

    def _validate_batch(self, *_a) -> None:
        """On focus-out, snap the batch field back to a non-negative number.

        ``_sync_settings`` falls back to 0 for garbage, but silently running
        with the engine default while the field still shows ``10a`` is a
        trap - correct the field itself and say why.
        """
        raw = self.var_batch.get().strip()
        if raw.isdigit():
            return
        self.var_batch.set("0")
        if raw:
            self.var_status.set("Batch: not a number - reset to 0 (0 = engine default)")

    def _row_matches(self, iid: str) -> bool:
        mode = self.var_filter.get()
        q = self.var_search.get().strip().lower()
        if mode != "All" and self.FILTER_OF_TAG.get(self._row_tag.get(iid, "")) != mode:
            return False
        if q:
            try:
                values = self.tree.set(iid)
            except tk.TclError:
                return True
            text = " ".join(str(v) for v in values.values()).lower()
            if q not in text:
                return False
        return True

    def _apply_result_filter(self) -> None:
        """Show only the rows matching the category and the search text.

        Treeview cannot hide rows, so non-matching items are detached and
        matching ones are re-attached in their original order; the master
        list keeps that order stable across repeated filtering.
        """
        idx = 0
        for iid in self._all_rows:
            try:
                if self._row_matches(iid):
                    self.tree.reattach(iid, "", idx)
                    idx += 1
                else:
                    self.tree.detach(iid)
            except tk.TclError:
                continue
        self.var_shown.set(f"{idx}/{len(self._all_rows)} rows")

    def _clear_filter(self) -> None:
        self.var_search.set("")
        self.var_filter.set("All")
        self._apply_result_filter()

    def _insert_result_row(self, values: tuple, tag: str) -> None:
        """Insert a result row honoring the active filter immediately."""
        iid = self.tree.insert("", "end", values=values, tags=(tag,) if tag else ())
        self._all_rows.append(iid)
        self._row_tag[iid] = tag
        if not self._row_matches(iid):
            self.tree.detach(iid)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _current_targets(self) -> list[PingTarget]:
        """Build the target list from the Targets selector.

        ``modern`` is the 2024-2026 verified list (18 endpoints, ~37x more
        real backlinks per request than the legacy list), ``legacy`` is the
        6,671-site list recovered from the original, ``both`` sends to the
        union exactly like the CLI's ``--modern`` without ``--no-legacy``.
        """
        mode = self.var_target_list.get()
        targets: list[PingTarget] = []
        if mode in ("legacy", "both"):
            sites = load_ping_sites()
            filt = self.var_site_filter.get().strip().lower()
            if filt:
                sites = [s for s in sites if filt in s.lower()]
            # Custom mode: only the first N sites of the ordered list
            # (the original's "custom mode" + "custom mode number").
            try:
                n = int(self.var_custom_number.get() or 0)
            except tk.TclError:
                n = 0
            sites = engine.apply_custom_mode(
                sites,
                Settings(custom_mode=self.var_custom.get(), custom_mode_number=n),
            )
            # "Deep link indexer only": the legacy list has no recovered
            # per-site deep-link capability, so only the proven sets stay -
            # the authority statistics hosts here, the modern list below.
            if self.var_deep_link.get():
                sites = []
            targets.extend(default_targets([] if self.var_stats_only.get() else sites))
        if mode in ("modern", "both"):
            targets.extend(load_modern_targets())
        if self.settings.max_sites:
            targets = targets[: self.settings.max_sites]
        return targets

    def _sync_settings(self) -> None:
        """Copy the panel widgets into ``self.settings`` (no side effects)."""
        self.settings.threads = max(1, int(self.var_threads.get() or 1))
        self.settings.check_url = self.var_check.get()
        self.settings.use_proxy = self.var_use_proxy.get()
        proxies = [p.strip() for p in self.var_proxy.get().split(",") if p.strip()]
        # SOCKS proxies are parsed but not usable by the engine; dropping
        # them here keeps a run from failing one request at a time.
        from . import proxies as proxy_mod

        proxies = [p for p in proxies if proxy_mod.is_supported_proxy(p)]
        self.settings.proxy_list = proxies
        try:
            self.settings.batch_size = max(0, int(self.var_batch.get() or 0))
        except ValueError:
            self.settings.batch_size = 0
        self.settings.site_filter = self.var_site_filter.get().strip()
        self.settings.statistics_only = self.var_stats_only.get()
        self.settings.custom_mode = self.var_custom.get()
        try:
            self.settings.custom_mode_number = max(1, int(self.var_custom_number.get() or 1))
        except (ValueError, tk.TclError):
            pass
        self.settings.deep_link_only = self.var_deep_link.get()
        self.settings.min_to_tray = self.var_min_tray.get()
        self.settings.fast_mode = self.var_fast.get()
        self.settings.repair_misses = self.var_repair.get()
        tl = self.var_target_list.get()
        if tl in ("legacy", "modern", "both"):
            self.settings.target_list = tl

    def _apply_settings(self) -> None:
        self._sync_settings()
        # Reuse the Indexer so the dead-host cache (the biggest speed-up on
        # a second run against the legacy list) survives; only the proxy
        # and user-agent pools genuinely start fresh.
        self.indexer.apply_settings(self.settings)
        # Persist so the next launch (GUI or CLI) starts from here.
        try:
            self.settings.save(CONFIG_PATH)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Queue resume (the original's last_queue.txt)
    # ------------------------------------------------------------------
    def _maybe_resume_queue(self) -> None:
        """Offer to reload URLs from an interrupted run, like the original.

        Skipped when a run is already going (the file is written at the
        start of every run, so asking then would be nonsense) and asked
        at most once per launch.
        """
        if getattr(self, "_resume_asked", False):
            return
        if self._worker and self._worker.is_alive():
            return
        try:
            urls = read_url_file(QUEUE_PATH)
        except OSError:
            return
        if not urls:
            return
        self._resume_asked = True
        if messagebox.askyesno(
            "Resume queue",
            f"{len(urls)} URL(s) from an interrupted run were found.\n"
            "Open them in the submit preview?",
        ):
            self._preview_urls(urls, source="interrupted run")

    def _host_of(self, url: str) -> str:
        """Extract the host from a URL for display, never raises."""
        try:
            return urlparse(url).hostname or url
        except Exception:
            return url

    def _format_link(self, res: engine.Result) -> tuple[str, str]:
        """Return (label, tag) describing the kind of link created.

        Error text is shortened to one line: a WinError message is several
        hundred characters long and would push the other columns out of
        sight.
        """
        if res.status == "submitted":
            if res.indexable:
                return "✓ indexed", "indexed"
            if res.listed:
                return "● listed", "listed"
            return "○ echo", "echo"
        if res.status == "failed":
            err = (res.error or f"HTTP {res.code}").split("\n", 1)[0]
            if len(err) > 60:
                err = err[:57] + "..."
            return f"failed: {err}", "failed"
        if res.status == "skipped":
            err = (res.error or "host dead").split("\n", 1)[0]
            if len(err) > 60:
                err = err[:57] + "..."
            return f"skipped: {err}", "skipped"
        return res.status, ""

    def _set_running(self, running: bool) -> None:
        """Toggle the toolbar buttons between idle and running states."""
        if running:
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
        else:
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _start_single(self) -> None:
        url = self.var_url.get().strip()
        if not url:
            return
        self._run([url])

    def _open_queue(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a URL list",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        urls = read_url_file(path)
        if not urls:
            messagebox.showwarning("No URLs", "That file did not contain any URLs.")
            return
        self._preview_urls(urls, source=path)

    def _import_clipboard(self) -> None:
        """Read URLs from the clipboard (one per line) into the preview."""
        try:
            raw = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Clipboard", "The clipboard is empty or not text.")
            return
        urls = [u.strip().strip('"') for u in raw.splitlines()]
        urls = [u for u in urls if u and not u.startswith("#")]
        if not urls:
            messagebox.showwarning("No URLs", "The clipboard did not contain any URLs.")
            return
        self._preview_urls(urls, source="clipboard")

    def _preview_urls(self, urls: list[str], source: str = "") -> None:
        """Editable list of URLs with a validation summary before a run.

        Mirrors the original's Multiple-URLs form: the parsed list can be
        corrected here (remove a bad line, paste another) instead of
        re-opening the source file.
        """
        win = tk.Toplevel(self)
        win.title("URLs to submit")
        win.transient(self)
        win.geometry("640x420")

        head = ttk.Frame(win, padding=(10, 10, 10, 4))
        head.pack(fill="x")
        info = tk.StringVar()
        ttk.Label(head, textvariable=info).pack(side="left")

        body = ttk.Frame(win, padding=(10, 0, 10, 4))
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, wrap="none", undo=True)
        ysb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ysb.set)
        txt.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        txt.insert("1.0", "\n".join(urls))

        def recount(*_a) -> None:
            lines = [l.strip() for l in txt.get("1.0", "end").splitlines()]
            lines = [l for l in lines if l and not l.startswith("#")]
            dupes = len(lines) - len(set(lines))
            bad = sum(1 for l in lines if not validate_url(l)[0])
            info.set(
                f"{len(lines)} URL(s)   {dupes} duplicate   {bad} invalid"
                + (f"   (from {source})" if source else "")
            )
            return

        txt.bind("<KeyRelease>", recount)
        recount()

        foot = ttk.Frame(win, padding=(10, 0, 10, 10))
        foot.pack(fill="x")
        ttk.Button(foot, text="Cancel", command=win.destroy).pack(side="right")

        def add_urls(new_urls: list[str]) -> None:
            """Append crawl results to the preview, skipping duplicates."""
            existing = {l.strip() for l in txt.get("1.0", "end").splitlines() if l.strip()}
            fresh = [u for u in new_urls if u not in existing]
            if fresh:
                if existing:
                    txt.insert("end", "\n")
                txt.insert("end", "\n".join(fresh))
            recount()

        ttk.Button(
            foot, text="Crawl online...", command=lambda: self._crawl_dialog(win, add_urls)
        ).pack(side="right", padx=6)
        ttk.Button(foot, text="Dedupe", command=lambda: self._dedupe_preview(txt, recount)).pack(
            side="right", padx=6
        )
        btn_run = ttk.Button(foot, text="Start submission")
        btn_run.pack(side="right")

        def start() -> None:
            lines = [l.strip() for l in txt.get("1.0", "end").splitlines()]
            lines = [l for l in lines if l and not l.startswith("#")]
            if not lines:
                messagebox.showwarning("No URLs", "The list is empty.", parent=win)
                return
            win.destroy()
            self._run(lines)

        btn_run.config(command=start)
        txt.focus_set()

    @staticmethod
    def _dedupe_preview(txt: tk.Text, recount) -> None:
        """Remove duplicate lines from the preview, keeping first order."""
        lines = [l.strip() for l in txt.get("1.0", "end").splitlines()]
        seen: set[str] = set()
        out: list[str] = []
        for l in lines:
            if l and l not in seen:
                seen.add(l)
                out.append(l)
        txt.delete("1.0", "end")
        txt.insert("1.0", "\n".join(out))
        recount()

    # ------------------------------------------------------------------
    # Crawl Online (the original's TForm11, opened from the TForm2-like
    # Multiple-URLs preview)
    # ------------------------------------------------------------------
    def _crawl_dialog(self, parent_win, on_add) -> None:
        """Crawl one seed URL for links, review them, feed the queue.

        The recovered TForm11 control set is mirrored: levels deep, with
        anchor text, external sites, same folder, use proxies.  Results
        appear in a URL/Anchor/DoFollow table first - the same columns the
        original's URL tree shows - and only join the queue on request.
        """
        from .crawler import Crawler

        win = tk.Toplevel(parent_win)
        win.title("Crawl for URLs")
        win.transient(parent_win)
        win.geometry("740x420")

        form = ttk.Frame(win, padding=(10, 10, 10, 4))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="URL:").grid(row=0, column=0, sticky="w")
        seed = tk.StringVar(value=self.var_url.get())
        ttk.Entry(form, textvariable=seed).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(form, text="Levels deep:").grid(row=0, column=2, sticky="e")
        depth = tk.IntVar(value=2)
        ttk.Spinbox(form, from_=1, to=10, textvariable=depth, width=4).grid(
            row=0, column=3, padx=(4, 0)
        )

        anchor = tk.BooleanVar(value=True)
        external = tk.BooleanVar(value=False)
        same_folder = tk.BooleanVar(value=False)
        use_proxies = tk.BooleanVar(value=False)
        opts = ttk.Frame(win, padding=(10, 0, 10, 4))
        opts.pack(fill="x")
        for col, (text, var) in enumerate(
            (
                ("With anchor text", anchor),
                ("External sites", external),
                ("Same folder", same_folder),
                ("Use proxies", use_proxies),
            )
        ):
            ttk.Checkbutton(opts, text=text, variable=var).grid(
                row=0, column=col, sticky="w", padx=(0, 16)
            )

        bar = ttk.Frame(win, padding=(10, 0, 10, 4))
        bar.pack(fill="x")
        btn_start = ttk.Button(bar, text="Start")
        btn_stop = ttk.Button(bar, text="Stop", state="disabled")
        btn_start.pack(side="left")
        btn_stop.pack(side="left", padx=6)
        status = tk.StringVar(value="Idle")
        ttk.Label(bar, textvariable=status).pack(side="left", padx=10)

        body = ttk.Frame(win, padding=(10, 0, 10, 4))
        body.pack(fill="both", expand=True)
        cols = ("url", "anchor", "dofollow", "level")
        tree = ttk.Treeview(body, columns=cols, show="headings")
        for cid, head, width, anc in (
            ("url", "URL", 350, "w"),
            ("anchor", "Anchor text", 170, "w"),
            ("dofollow", "DoFollow", 60, "center"),
            ("level", "Level", 50, "center"),
        ):
            tree.heading(cid, text=head)
            tree.column(cid, width=width, anchor=anc)
        ysb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)
        tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        foot = ttk.Frame(win, padding=(10, 0, 10, 10))
        foot.pack(fill="x")
        btn_add = ttk.Button(foot, text="Add URLs to queue", state="disabled")
        btn_copy = ttk.Button(foot, text="Copy TSV", state="disabled")
        btn_close = ttk.Button(foot, text="Close")
        btn_add.pack(side="right")
        btn_copy.pack(side="right", padx=6)
        btn_close.pack(side="right", padx=6)

        found: queue.Queue = queue.Queue()  # (url, anchor, dofollow, level)
        holder: dict = {"crawler": None, "thread": None, "pages": 0}

        def drain() -> None:
            while True:
                try:
                    u, a, df, lv = found.get_nowait()
                except queue.Empty:
                    return
                tree.insert("", "end", values=(u, a, "yes" if df else "no", f"L{lv}"))

        def poll() -> None:
            drain()
            alive = holder["thread"] is not None and holder["thread"].is_alive()
            if alive:
                status.set(f"{holder['pages']} page(s), {len(tree.get_children())} URL(s) ...")
                win.after(150, poll)
            elif holder["thread"] is not None:
                drain()
                status.set(f"Done - {holder['pages']} page(s), {len(tree.get_children())} URL(s)")
                btn_start.config(state="normal")
                btn_stop.config(state="disabled")
                if len(tree.get_children()):
                    btn_add.config(state="normal")
                    btn_copy.config(state="normal")
                holder["thread"] = None

        def start() -> None:
            url = seed.get().strip()
            if not url:
                messagebox.showwarning("Crawl for URLs", "Enter a seed URL first.", parent=win)
                return
            proxy = None
            if use_proxies.get():
                from . import proxies as proxy_mod

                candidates = [
                    p.strip()
                    for p in self.var_proxy.get().split(",")
                    if p.strip() and proxy_mod.is_supported_proxy(p)
                ]
                if candidates:
                    proxy = candidates[0]
                else:
                    messagebox.showinfo(
                        "Crawl for URLs", "No proxy configured - crawling direct.", parent=win
                    )
            try:
                crawler = Crawler(
                    url,
                    depth=max(1, depth.get()),
                    external=external.get(),
                    same_folder=same_folder.get(),
                    with_anchor_text=anchor.get(),
                    proxy=proxy,
                )
            except ValueError as exc:
                messagebox.showerror("Crawl for URLs", str(exc), parent=win)
                return
            tree.delete(*tree.get_children())
            holder["crawler"] = crawler
            holder["pages"] = 0
            btn_start.config(state="disabled")
            btn_stop.config(state="normal")
            btn_add.config(state="disabled")
            btn_copy.config(state="disabled")
            status.set("Fetching the seed page ...")

            def on_page(stats, just_found):
                holder["pages"] = stats.pages_fetched
                for u in just_found:
                    found.put((u.url, u.anchor, u.dofollow, u.level))

            def work() -> None:
                try:
                    crawler.run(on_page=on_page)
                except Exception:  # noqa: BLE001 - the poll shows what was found
                    pass

            t = threading.Thread(target=work, daemon=True)
            holder["thread"] = t
            t.start()
            poll()

        def stop() -> None:
            if holder["crawler"] is not None:
                holder["crawler"].stop()

        def add_to_queue() -> None:
            urls = [tree.item(iid, "values")[0] for iid in tree.get_children()]
            if not urls:
                return
            on_add(urls)
            win.destroy()

        def copy_tsv() -> None:
            lines = [
                "\t".join(str(v) for v in tree.item(iid, "values"))
                for iid in tree.get_children()
            ]
            win.clipboard_clear()
            win.clipboard_append("\n".join(lines))
            status.set(f"Copied {len(lines)} row(s) as TSV")

        def close() -> None:
            stop()
            win.destroy()

        btn_start.config(command=start)
        btn_stop.config(command=stop)
        btn_add.config(command=add_to_queue)
        btn_copy.config(command=copy_tsv)
        btn_close.config(command=close)
        win.protocol("WM_DELETE_WINDOW", close)
        # exposed so the work/ test scripts can drive the dialog directly
        win.seed = seed  # type: ignore[attr-defined]
        win.depth = depth  # type: ignore[attr-defined]
        win.tree = tree  # type: ignore[attr-defined]
        win.status = status  # type: ignore[attr-defined]
        win.crawl_buttons = (btn_start, btn_stop, btn_add, btn_copy)  # type: ignore[attr-defined]
        win.crawl_holder = holder  # type: ignore[attr-defined]
        win._crawl_start = start  # type: ignore[attr-defined]
        return win

    def _run(self, urls: list[str]) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        # Reset the run-scoped counters before anything is queued, so the
        # skip events emitted by validation below are counted from zero.
        self.tree.delete(*self.tree.get_children())
        self._all_rows = []
        self._row_tag = {}
        self.var_shown.set("0 rows")
        self._backlinks = 0
        self._validation_skipped = 0
        self.var_backlinks.set("Backlinks: 0")
        self.progress.configure(maximum=1, value=0)
        self._svc_run = False
        self._svc_progress = 0

        # Reject obviously broken URLs before spending thousands of
        # requests on them.  The reason is surfaced back to the user, so
        # a typo in a 100-URL list is fixed once, not discovered 6,676
        # times per bad entry.
        good: list[str] = []
        for u in urls:
            ok, reason = validate_url(u)
            if ok:
                good.append(u)
            else:
                self._events.put(("skip", u, reason))
        invalid = len(urls) - len(good)
        self.var_validated.set(
            f"{len(good)} valid" + (f", {invalid} invalid" if invalid else "")
        )
        if not good:
            self.var_status.set("No valid URLs to submit")
            messagebox.showwarning(
                "No valid URLs", "None of the entered URLs passed validation."
            )
            return
        self._apply_settings()
        targets = self._current_targets()
        if not targets:
            messagebox.showwarning("No sites", "No indexing sites are available.")
            return
        msg = f"Submitting {len(good)} URL(s) to {len(targets)} site(s) ..."
        if invalid:
            msg += f"  ({invalid} invalid skipped)"
        self.var_status.set(msg)
        self._set_running(True)

        # Read every Tk variable here, on the main thread.  Tk variables are
        # not thread-safe: calling .get() from the worker raises
        # "main thread is not in main loop" as soon as the interpreter is
        # not sitting in a main loop at that moment.
        want_repair = self.var_repair.get()
        want_fast = self.var_fast.get()
        want_check = self.settings.check_url

        # Publish a correct maximum straight away.  run_adaptive keeps one
        # target per host, so len(targets) overstates the work by 2x on the
        # legacy list (13,356 targets -> 6,676 hosts) and the bar used to
        # stall at 50%; the engine re-publishes the exact total anyway.
        if want_fast and not (self.settings.batch_size > 1 and want_repair):
            expected = len(good) * len(engine.dedupe_by_host(targets))
        else:
            expected = len(good) * len(targets)
        self.progress.configure(maximum=max(1, expected), value=0)

        # Queue persistence, like the original's last_queue.txt: the list
        # survives a crash or a Stop, and the next launch offers to resume.
        try:
            QUEUE_PATH.write_text("\n".join(good) + "\n", encoding="utf-8")
        except OSError:
            pass

        def on_result(res: engine.Result) -> None:
            self._events.put(("result", res))

        def worker() -> None:
            try:
                if want_check:
                    ok: list[str] = []
                    for u in good:
                        url_ok, reason = self.indexer.check_url(u)
                        if url_ok:
                            ok.append(u)
                        else:
                            self._events.put(("skip", u, reason))
                    urls_to_use = ok
                else:
                    urls_to_use = good
                if urls_to_use:
                    batch = self.settings.batch_size
                    if batch > 1 and want_repair:
                        self.indexer.run_batched(
                            urls_to_use, targets, on_result=on_result, batch_size=batch
                        )
                    elif want_fast:
                        self.indexer.run_adaptive(
                            urls_to_use, targets, on_result=on_result, batch_size=batch
                        )
                    else:
                        self.indexer.run(
                            urls_to_use, targets, on_result=on_result, batch_size=batch
                        )
            except Exception as exc:  # noqa: BLE001
                self._events.put(("error", str(exc)))
            finally:
                self._events.put(("done", None))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self.indexer.stop()
            self.var_status.set("Stopping ...")

    def _poll(self) -> None:
        """Drain the event queue and refresh the UI once per tick.

        The counters are recomputed after the loop, not per event: a run of
        50 URLs against 6,676 sites produces 333,800 results, and one
        ``progressbar.configure()`` per result costs more than the tree
        insertion itself.  The loop therefore only touches the tree and a
        counter, and the widgets are updated once at the end.
        """
        inserted = 0
        try:
            while True:
                kind, *payload = self._events.get_nowait()
                if kind == "result":
                    res: engine.Result = payload[0]
                    link_label, tag = self._format_link(res)
                    self._insert_result_row(
                        (
                            res.target,
                            self._host_of(res.url),
                            link_label,
                            res.code,
                            res.elapsed_ms,
                        ),
                        tag,
                    )
                    if res.indexable or res.listed:
                        self._backlinks += 1
                    inserted += 1
                elif kind == "skip":
                    url, reason = payload[0], payload[1]
                    self._insert_result_row(
                        (url, "", f"skipped: {reason}", "", ""), "skipped"
                    )
                    self._validation_skipped += 1
                    inserted += 1
                elif kind == "proxy_result":
                    found = payload[0]
                    self.var_proxy.set(", ".join(p.url for p in found))
                    self.var_use_proxy.set(bool(found))
                    self.var_status.set(f"{len(found)} working proxy/proxies loaded")
                elif kind == "proxy_error":
                    self.var_status.set("Proxy scrape failed")
                    messagebox.showerror("Proxy scrape", str(payload[0]))
                elif kind == "proxy_tested":
                    kept, total = payload[0], payload[1]
                    self.var_proxy.set(", ".join(p.url for p in kept))
                    self.var_use_proxy.set(bool(kept))
                    self.var_status.set(
                        f"{len(kept)}/{total} proxy/proxies passed the live test"
                    )
                elif kind == "error":
                    self.var_status.set("Error: " + str(payload[0]))
                    messagebox.showerror("Error", str(payload[0]))
                elif kind == "svc_done":
                    total_n, ok_n = payload[0]
                    self.var_status.set(
                        f"Done - {ok_n}/{total_n} index service submission(s) ok"
                    )
                    self.var_queue.set("Q: 0")
                    self._set_running(False)
                    self.progress.configure(value=self.progress["maximum"])
                    self._svc_run = False
                elif kind == "done":
                    s = self.indexer.stats
                    self.var_status.set(
                        f"Done - {s.submitted} submitted, {s.failed} failed, "
                        f"{s.skipped + self._validation_skipped} skipped, "
                        f"{s.links_per_minute:.1f} LpM, "
                        f"{self._backlinks} backlink(s)"
                    )
                    self.var_queue.set("Q: 0")
                    self.var_lpm.set(f"LpM: {s.links_per_minute:.1f}")
                    self.var_backlinks.set(f"Backlinks: {self._backlinks}")
                    self._set_running(False)
                    if s.total > 0:
                        self.progress.configure(maximum=s.total, value=s.total)
                    # A clean finish empties the resume queue; a stopped or
                    # crashed run leaves it for the next launch.
                    if not self.indexer.stopped and not self._svc_run:
                        try:
                            QUEUE_PATH.unlink(missing_ok=True)
                        except OSError:
                            pass
        except queue.Empty:
            pass

        if inserted:
            if self._svc_run:
                self._svc_progress += inserted
                self.progress.configure(
                    value=min(self._svc_progress, self.progress["maximum"])
                )
                self.var_queue.set(
                    f"Q: {max(0, self.progress['maximum'] - self._svc_progress)}"
                )
            else:
                s = self.indexer.stats
                # Count everything finished, including validation rejects that
                # never reached the engine, so the bar does not lag behind the
                # "skipped" rows already visible in the tree.
                done = s.submitted + s.failed + s.skipped + self._validation_skipped
                # The repair pass raises stats.total mid-run, so maximum is
                # re-published on every tick rather than fixed at start-up.
                if s.total > 0:
                    self.progress.configure(maximum=s.total, value=min(done, s.total))
                self.var_queue.set(f"Q: {max(0, s.total - done)}")
                self.var_lpm.set(f"LpM: {s.links_per_minute:.1f}")
            self.var_backlinks.set(f"Backlinks: {self._backlinks}")
            self.var_shown.set(
                f"{len(self.tree.get_children())}/{len(self._all_rows)} rows"
            )

        self.after(150, self._poll)

    def _import_file(self) -> None:
        self._open_queue()

    def _scrape_proxies(self) -> None:
        from . import proxies as proxy_mod

        self.var_status.set("Scraping proxies ...")
        self.update_idletasks()

        def work() -> None:
            try:
                found = proxy_mod.scrape_proxies(limit=15)
            except Exception as exc:  # noqa: BLE001
                self._events.put(("proxy_error", str(exc)))
                return
            self._events.put(("proxy_result", found))

        threading.Thread(target=work, daemon=True).start()

    def _test_proxies(self) -> None:
        """Pre-run live test for the proxies typed or loaded into the field.

        Scraped proxies were tested when collected; this covers the manual
        sources (--proxy equivalents).  Only the ones that answer stay in
        the field; if none survive, the Use switch is turned off so the run
        does not burn every request on dead proxies.
        """
        from . import proxies as proxy_mod

        raw = [p.strip() for p in self.var_proxy.get().split(",") if p.strip()]
        if not raw:
            self.var_status.set("No proxies to test")
            return
        parsed = [p for p in (proxy_mod.parse_proxy_line(x) for x in raw) if p]
        if not parsed:
            self.var_status.set("No parsable proxies to test")
            return
        self.var_status.set(f"Testing {len(parsed)} proxies ...")
        self.update_idletasks()

        def work() -> None:
            try:
                kept = proxy_mod.find_working_proxies(parsed, limit=max(1, len(parsed)))
            except Exception as exc:  # noqa: BLE001
                self._events.put(("proxy_error", str(exc)))
                return
            self._events.put(("proxy_tested", (kept, len(parsed))))

        threading.Thread(target=work, daemon=True).start()

    def _load_proxy_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a proxy list",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            lines = [
                l.strip()
                for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
        except OSError as exc:
            messagebox.showerror("Proxy list", str(exc))
            return
        self.var_proxy.set(", ".join(lines))
        self.var_use_proxy.set(bool(lines))
        self.var_status.set(f"{len(lines)} proxy/proxies loaded")

    def _show_services(self) -> None:
        """Manage the paid index services: enable, test, run, edit.

        This used to be a read-only text dump while ``services.py`` had a
        complete submission implementation - the original program's TForm3
        has Enabled/Edit/Delete/Test, and this dialog restores that role.
        """
        from . import services as service_mod

        keys = service_mod.load_keys()

        win = tk.Toplevel(self)
        win.title("Indexing services")
        win.transient(self)
        win.geometry("760x440")

        top = ttk.Frame(win, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(
            top,
            text="Paid third-party indexing services (index_service\\*.ini).\n"
            "Test sends the URL from the main window to the selected service; "
            "Run submits to every enabled service.",
            foreground="#555",
        ).pack(side="left")

        body = ttk.Frame(win, padding=(10, 0, 10, 4))
        body.pack(fill="both", expand=True)
        cols = ("name", "enabled", "key", "rating", "costs", "desc")
        tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        tree.heading("name", text="Service")
        tree.heading("enabled", text="Enabled")
        tree.heading("key", text="Key")
        tree.heading("rating", text="Rating")
        tree.heading("costs", text="Costs")
        tree.heading("desc", text="Description")
        tree.column("name", width=150)
        tree.column("enabled", width=70, anchor="center")
        tree.column("key", width=45, anchor="center")
        tree.column("rating", width=55, anchor="center")
        tree.column("costs", width=130)
        tree.column("desc", width=320)
        ysb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)
        tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        tree.tag_configure("on", foreground="#0b6b0b")
        tree.tag_configure("off", foreground="#6e6e6e")

        def rebuild() -> None:
            svcs = service_mod.load_services(SERVICE_DIR)
            tree.delete(*tree.get_children())
            for s in svcs:
                tree.insert(
                    "",
                    "end",
                    iid=s.name,
                    values=(s.name, "✓" if s.enabled else "–",
                            "✓" if s.name in keys else "–", s.rating or "-",
                            s.costs or "-", s.desc or ""),
                    tags=("on" if s.enabled else "off"),
                )
            n_on = sum(1 for s in svcs if s.enabled)
            count.set(f"{len(svcs)} services, {n_on} enabled")

        count = tk.StringVar()
        status_lbl = ttk.Label(win, textvariable=count, padding=(10, 0, 10, 0))
        status_lbl.pack(fill="x")
        rebuild()

        def selected_service():
            sel = tree.selection()
            if not sel:
                return None
            svcs = {s.name: s for s in service_mod.load_services(SERVICE_DIR)}
            return svcs.get(sel[0])

        def toggle() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Indexing services", "Select a service first.", parent=win)
                return
            name = sel[0]
            svcs = {s.name: s for s in service_mod.load_services(SERVICE_DIR)}
            svc = svcs.get(name)
            if svc is None:
                return
            new = not svc.enabled
            try:
                service_mod.set_service_enabled(svc.path, new)
            except OSError as exc:
                messagebox.showerror("Indexing services", str(exc), parent=win)
                return
            rebuild()

        def edit_ini() -> None:
            svc = selected_service()
            if svc is None:
                messagebox.showinfo("Indexing services", "Select a service first.", parent=win)
                return
            try:
                import os

                os.startfile(svc.path)  # noqa: S606 - open in the default editor
            except OSError as exc:
                messagebox.showerror("Indexing services", str(exc), parent=win)

        def target_urls() -> list[str]:
            u = self.var_url.get().strip()
            return [u] if u else []

        def run_services(which: list) -> None:
            """Submit the main-window URL to each service on a worker thread."""
            urls = target_urls()
            if not urls:
                messagebox.showwarning(
                    "Indexing services",
                    "Enter a URL in the main window first.",
                    parent=win,
                )
                return
            if self._worker and self._worker.is_alive():
                messagebox.showinfo("Busy", "A run is already in progress.")
                return

            def work() -> None:
                ok_count = 0
                for svc in which:
                    if self.indexer.stopped:
                        break
                    try:
                        ok, msg = svc.submit(urls, service_mod.service_arguments_with_keys(svc, keys))
                    except Exception as exc:  # noqa: BLE001
                        ok, msg = False, str(exc)[:160]
                    if ok:
                        ok_count += 1
                    res = engine.Result(
                        target=urls[0],
                        url=svc.url or svc.name,
                        status="submitted" if ok else "failed",
                        code=0,
                        elapsed_ms=0,
                        error="" if ok else msg,
                    )
                    with self.indexer._lock:
                        self.indexer.results.append(res)
                    self._events.put(("result", res))
                self._events.put(("svc_done", (len(which), ok_count)))

            self._svc_run = True
            self.progress.configure(maximum=max(1, len(which)), value=0)
            self.var_status.set(f"Submitting to {len(which)} index service(s) ...")
            self._set_running(True)
            self._worker = threading.Thread(target=work, daemon=True)
            self._worker.start()
            win.destroy()

        def test_one() -> None:
            svc = selected_service()
            if svc is None:
                messagebox.showinfo("Indexing services", "Select a service first.", parent=win)
                return
            run_services([svc])

        def run_enabled() -> None:
            enabled = [s for s in service_mod.load_services(SERVICE_DIR) if s.enabled]
            if not enabled:
                messagebox.showwarning(
                    "Indexing services", "No service is enabled.", parent=win
                )
                return
            run_services(enabled)

        def set_key() -> None:
            """Edit the stored credentials for the selected service."""
            svc = selected_service()
            if svc is None:
                messagebox.showinfo("Indexing services", "Select a service first.", parent=win)
                return
            dlg = tk.Toplevel(win)
            dlg.title(f"API key - {svc.name}")
            dlg.transient(win)
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=12)
            frm.pack(fill="both", expand=True)
            ttk.Label(
                frm,
                text=f"Credentials for {svc.name} are kept in keys.json next to\n"
                "config.ini and are never written to reports.",
                foreground="#555",
            ).grid(row=0, column=0, columnspan=2, pady=(0, 8))
            stored = keys.get(svc.name, {})
            entries: dict[str, tk.Entry] = {}
            for i, (var, meta) in enumerate(svc.variables.items(), start=1):
                ttk.Label(frm, text=var + ":").grid(row=i, column=0, sticky="w", pady=3)
                ent = ttk.Entry(frm, width=34, show="•" if meta.get("type") == "password" else "")
                ent.insert(0, stored.get(var, ""))
                ent.grid(row=i, column=1, pady=3, padx=(6, 0))
                hint = meta.get("hint", "")
                if hint:
                    ttk.Label(frm, text=hint[:70], foreground="#777").grid(
                        row=i, column=2, sticky="w", padx=(6, 0)
                    )
                entries[var] = ent

            def save() -> None:
                for var, ent in entries.items():
                    value = ent.get().strip()
                    if value:
                        keys.setdefault(svc.name, {})[var] = value
                    elif svc.name in keys:
                        keys[svc.name].pop(var, None)
                if not keys.get(svc.name):
                    keys.pop(svc.name, None)
                try:
                    service_mod.save_keys(keys)
                except OSError as exc:
                    messagebox.showerror("Indexing services", str(exc), parent=dlg)
                    return
                dlg.destroy()
                rebuild()

            row = len(entries) + 1
            ttk.Button(frm, text="Save", command=save).grid(row=row, column=1, sticky="e", pady=(8, 0))
            ttk.Button(frm, text="Cancel", command=dlg.destroy).grid(
                row=row, column=2, sticky="e", pady=(8, 0)
            )
            dlg.bind("<Escape>", lambda _e: dlg.destroy())

        btns = ttk.Frame(win, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="Toggle enabled", command=toggle).pack(side="left")
        ttk.Button(btns, text="Set key...", command=set_key).pack(side="left", padx=6)
        ttk.Button(btns, text="Test", command=test_one).pack(side="left")
        ttk.Button(btns, text="Run all enabled", command=run_enabled).pack(side="left")
        ttk.Button(btns, text="Edit .ini...", command=edit_ini).pack(side="right")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=6)

    def _export_report(self) -> None:
        if not self.indexer.results:
            messagebox.showinfo("Nothing to export", "Run a submission first.")
            return
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Save report",
            initialdir=str(REPORT_DIR),
            initialfile="report.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
        )
        if not path:
            return
        self.settings.report_format = "json" if path.lower().endswith(".json") else "csv"
        self.indexer.settings = self.settings
        self.indexer.write_report(path)
        self.var_status.set(f"Report written to {path}")

    def _advanced_settings(self) -> None:
        """Engine knobs that do not belong on the main window.

        Every field here was previously unreachable from the GUI (CLI-only
        flags); the values are persisted to config.ini so they survive a
        restart and apply to the CLI as well.
        """
        win = tk.Toplevel(self)
        win.title("Advanced settings")
        win.transient(self)
        win.resizable(False, False)

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        numbers: list[tuple[str, tk.StringVar, str]] = [
            ("Request timeout (s)", tk.StringVar(value=f"{self.settings.timeout:g}"), "timeout"),
            ("Connect timeout (s)", tk.StringVar(value=f"{self.settings.connect_timeout:g}"), "connect_timeout"),
            ("Read timeout (s)", tk.StringVar(value=f"{self.settings.read_timeout:g}"), "read_timeout"),
            ("Delay after each request (s)", tk.StringVar(value=f"{self.settings.delay:g}"), "delay"),
            ("Max sites (0 = all)", tk.StringVar(value=str(self.settings.max_sites)), "max_sites"),
        ]
        for row, (label, var, _attr) in enumerate(numbers):
            ttk.Label(frm, text=label + ":").grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=var, width=10).grid(row=row, column=1, sticky="w", pady=3)

        flags: list[tuple[str, tk.BooleanVar, str]] = [
            ("Follow redirects", tk.BooleanVar(value=self.settings.follow_redirects), "follow_redirects"),
            ("Skip hosts that failed in this run", tk.BooleanVar(value=self.settings.skip_dead_hosts), "skip_dead_hosts"),
            ("Random micro-delay between requests", tk.BooleanVar(value=self.settings.random_delay), "random_delay"),
            ("Probe hosts first (preflight)", tk.BooleanVar(value=self.settings.preflight), "preflight"),
            ("Reuse connections (pool)", tk.BooleanVar(value=self.settings.reuse_connections), "reuse_connections"),
            ("Backlink column in report", tk.BooleanVar(value=self.settings.report_with_link_type), "report_with_link_type"),
        ]
        for row, (label, var, _attr) in enumerate(flags):
            ttk.Checkbutton(frm, text=label, variable=var).grid(
                row=row, column=2, sticky="w", pady=3, padx=(24, 0)
            )

        btns = ttk.Frame(frm)
        btns.grid(row=max(len(numbers), len(flags)), column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=lambda: save()).pack(side="right", padx=6)

        def save() -> None:
            for label, var, attr in numbers:
                raw = var.get().strip()
                try:
                    val = float(raw) if attr != "max_sites" else int(raw or 0)
                except ValueError:
                    messagebox.showerror("Advanced settings", f"{label}: not a number", parent=win)
                    return
                if attr == "max_sites":
                    setattr(self.settings, attr, max(0, int(val)))
                else:
                    setattr(self.settings, attr, max(0.0, val))
            for _label, var, attr in flags:
                setattr(self.settings, attr, var.get())
            try:
                self.settings.save(CONFIG_PATH)
            except OSError as exc:
                messagebox.showerror("Advanced settings", str(exc), parent=win)
            self.var_status.set("Settings saved to config.ini")
            win.destroy()

    def _searchable_list(self, title: str, rows: list[tuple[str, str]], headers: tuple[str, str]) -> None:
        """A read-only list window with a live search box and a match count.

        Replaces the raw ``tk.Text`` dumps the Site list and Statistics
        viewers used to be: 6,671 lines with no way to find anything.
        """
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.geometry("680x420")

        bar = ttk.Frame(win, padding=(10, 10, 10, 4))
        bar.pack(fill="x")
        q = tk.StringVar()
        count = tk.StringVar(value=f"{len(rows)} rows")
        ttk.Label(bar, text="Search:").pack(side="left")
        ent = ttk.Entry(bar, textvariable=q)
        ent.pack(side="left", fill="x", expand=True, padx=(6, 10))
        ttk.Label(bar, textvariable=count).pack(side="right")

        body = ttk.Frame(win, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=headers, show="headings")
        tree.heading(headers[0], text=headers[0])
        tree.heading(headers[1], text=headers[1])
        tree.column(headers[0], width=340)
        tree.column(headers[1], width=260)
        ysb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)
        tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        def refill(*_a) -> None:
            needle = q.get().strip().lower()
            tree.delete(*tree.get_children())
            shown = 0
            for a, b in rows:
                if needle and needle not in a.lower() and needle not in (b or "").lower():
                    continue
                tree.insert("", "end", values=(a, b))
                shown += 1
            count.set(f"{shown}/{len(rows)} rows")

        # A write trace catches every change (typing, paste, programmatic)
        # where a key binding misses clipboard drops.
        q.trace_add("write", refill)
        refill()
        ent.focus_set()

    def _show_statistics(self) -> None:
        rows: list[tuple[str, str]] = []
        for base, path in engine.STATISTICS_TARGETS:
            rows.append((base + path, "path form"))
        for base in engine.QUERY_STATISTICS_TARGETS:
            rows.append((base, "query form (?url=)"))
        self._searchable_list("Statistics services", rows, ("URL", "Form"))

    def _show_about(self) -> None:
        """Application name, version and developer credits."""
        win = tk.Toplevel(self)
        win.title(f"About {engine.APP_NAME}")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=(24, 18))
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=engine.APP_NAME, font=("Segoe UI", 13, "bold")).pack()
        ttk.Label(frm, text=f"Version {engine.APP_VERSION}").pack(pady=(2, 10))
        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(0, 10))
        ttk.Label(frm, text=f"Developed by {engine.APP_DEVELOPER}").pack()
        ttk.Label(frm, text=f"© {engine.APP_YEAR}").pack(pady=(2, 10))
        link = ttk.Label(
            frm,
            text=engine.APP_URL,
            foreground="#0066cc",
            cursor="hand2",
        )
        link.pack()
        link.bind(
            "<Button-1>",
            lambda _e: webbrowser.open_new(engine.APP_URL),
        )
        link.bind(
            "<Enter>",
            lambda _e: link.configure(foreground="#004499", font=("Segoe UI", 9, "underline")),
        )
        link.bind(
            "<Leave>",
            lambda _e: link.configure(foreground="#0066cc", font=("Segoe UI", 9)),
        )
        ttk.Label(frm, text=engine.APP_DESCRIPTION, justify="center", wraplength=380).pack()
        ttk.Button(frm, text="Close", command=win.destroy).pack(pady=(14, 0))
        win.bind("<Escape>", lambda _e: win.destroy())

    def _show_sites(self) -> None:
        sites = load_ping_sites()
        dead = self.indexer.host_cache
        rows = []
        for s in sites:
            host = s.split("//", 1)[-1].split("/", 1)[0]
            state = "dead" if dead.is_dead(host) else ""
            rows.append((s, state))
        self._searchable_list("Ping site list", rows, ("Site", "Status"))

    def _start_emulator(self) -> None:
        from . import emulator as emu_mod

        win = tk.Toplevel(self)
        win.title("Index service emulator")
        win.geometry("420x240")
        win.transient(self)

        host = tk.StringVar(value=emu_mod.DEFAULT_HOST)
        port = tk.StringVar(value=str(emu_mod.DEFAULT_PORT))
        auth = tk.BooleanVar(value=False)
        login = tk.StringVar(value="")
        password = tk.StringVar(value="")
        api_key = tk.StringVar(value="")
        status = tk.StringVar(value="Stopped")

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Host:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=host, width=22).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(frm, text="Port:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=port, width=22).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(frm, text="Require Authorization", variable=auth).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=3
        )
        ttk.Label(frm, text="Login:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=login, width=22).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(frm, text="Password:").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=password, width=22, show="•").grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Label(frm, text="API-Key:").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=api_key, width=22, show="•").grid(row=5, column=1, sticky="ew", pady=3)
        ttk.Label(frm, textvariable=status).grid(row=6, column=0, columnspan=2, sticky="w", pady=8)

        holder = {"server": None}

        def start() -> None:
            if holder["server"] is not None:
                return
            cfg = emu_mod.build_config(
                SERVICE_DIR,
                host=host.get().strip() or emu_mod.DEFAULT_HOST,
                port=int(port.get() or emu_mod.DEFAULT_PORT),
                with_auth=auth.get(),
                login=login.get(),
                password=password.get(),
                api_key=api_key.get(),
            )
            server = emu_mod.IndexServiceEmulator(cfg)
            try:
                h, p = server.start()
            except OSError as exc:
                messagebox.showerror("Emulator", "Could not bind the port:" + chr(10) + str(exc))
                return
            holder["server"] = server
            status.set(f"Running on http://{h}:{p}  ({len(cfg.services)} services)")
            btn_start.config(state="disabled")
            btn_stop.config(state="normal")

        def stop() -> None:
            if holder["server"] is not None:
                holder["server"].stop()
                holder["server"] = None
            status.set("Stopped")
            btn_start.config(state="normal")
            btn_stop.config(state="disabled")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="w")
        btn_start = ttk.Button(btns, text="Start", command=start)
        btn_start.pack(side="left", padx=4)
        btn_stop = ttk.Button(btns, text="Stop", command=stop, state="disabled")
        btn_stop.pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", stop)

    def _quit(self) -> None:
        self.indexer.stop()
        if self._tray is not None:
            self._tray.close()
            self._tray = None
        # Persist the current panel state so the next launch starts here.
        try:
            self._sync_settings()
            self.settings.save(CONFIG_PATH)
        except Exception:  # noqa: BLE001 - never block shutdown on a save
            pass
        self.destroy()


def _enable_windows_dpi_awareness() -> None:
    """Ask Windows to render at the native DPI instead of bitmap-stretching.

    Must run before the Tk root exists.  SYSTEM_DPI_AWARE keeps scaling
    simple and reliable for a single-window app; without it every font is
    blurred on 125/150% scaled displays.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except OSError:
            pass


def main() -> None:
    _enable_windows_dpi_awareness()
    app = IndexerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
