from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from urllib.parse import quote
from tkinter import filedialog, messagebox, ttk

from spotify_ipod.config import AppConfig, PlaylistConfig, load_config, save_config
from spotify_ipod.lock import SyncLock, SyncLockError
from spotify_ipod.logging_setup import get_logger, setup_logging
from spotify_ipod.pipeline import SyncPipeline
from spotify_ipod.scheduler import agent_installed, install_agent, uninstall_agent
from spotify_ipod.overrides_ui import OverrideDialog
from spotify_ipod.spotify import SpotifyClient, merge_saved_playlists, parse_playlist_id, playlist_share_url, saved_playlist_entry


BG = "#11151c"
PANEL = "#1b222d"
BORDER = "#303b4c"
INK = "#f1f5f9"
MUTED = "#a7b5c7"
ACCENT = "#86efac"


def _broken_tk_message() -> str | None:
    blob = " ".join((sys.executable, sys.prefix, sys.base_prefix))
    if "CommandLineTools" in blob or "/Library/Developer/" in blob:
        return (
            "macOS Command Line Tools Python cannot open this window "
            "(Tk aborts on macOS 15).\n"
            "Run:  .venv/bin/python -m spotify_ipod setup"
        )
    return None


def launch_setup() -> None:
    setup_logging()
    problem = _broken_tk_message()
    if problem:
        print(problem, file=sys.stderr)
        raise SystemExit(2)
    app = SetupApp()
    app.mainloop()


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify → Music Sync")
        self.geometry("1000x680")
        self.minsize(810, 540)
        self.busy = False
        self.operation_buttons = []
        self.config_data = load_config()
        self.playlist_vars: dict[str, tk.BooleanVar] = {}
        self.remote_playlists: list[dict] = []
        self.report_callback_exception = self._on_callback_exception
        self._build()
        self._load_into_form()
        self.after(200, self._bring_forward)

    def _build(self) -> None:
        self.configure(bg=BG)
        self.option_add("*Font", "TkDefaultFont")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=BORDER,
                        foreground=INK, arrowcolor=INK, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", INK)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", INK)])
        style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=INK)

        for name, background, foreground in (
            ("Dashboard.TButton", BORDER, INK), ("Primary.TButton", ACCENT, BG),
        ):
            style.configure(name, background=background, foreground=foreground,
                            bordercolor=background, lightcolor=background, darkcolor=background,
                            padding=(12, 8), relief="flat")
            style.map(name, background=[("disabled", PANEL), ("active", ACCENT)],
                      foreground=[("disabled", MUTED), ("active", BG)],
                      bordercolor=[("focus", ACCENT)])
        style.configure("Dashboard.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=INK, rowheight=32)
        style.map("Dashboard.Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", BG)])
        style.configure("Dashboard.Treeview.Heading", background=BORDER, foreground=INK)
        style.map("Dashboard.Treeview.Heading", background=[("active", BORDER)])
        style.configure("Dashboard.TCheckbutton", background=PANEL, foreground=INK,
                        indicatorbackground=BG, indicatorforeground=BG, padding=4)
        style.map("Dashboard.TCheckbutton", background=[("active", PANEL)],
                  indicatorbackground=[("selected", ACCENT)])

        sidebar = tk.Frame(self, bg=PANEL, width=192, highlightbackground=BORDER, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._label(sidebar, "Spotify to Music", size=17, bold=True).pack(anchor="w", padx=18, pady=(28, 4))
        self._label(sidebar, "iPod sync", color=MUTED).pack(anchor="w", padx=18, pady=(0, 28))
        self.views = {}
        self.nav = {}
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)
        for name in ("Library", "Search Spotify", "Activity", "Settings"):
            self.nav[name] = self._button(sidebar, name, lambda name=name: self._show(name))
            self.nav[name].pack(fill="x", padx=12, pady=3)
            self.views[name] = tk.Frame(self.content, bg=BG)
        self.operation = tk.StringVar(value="Ready")
        self._label(sidebar, textvariable=self.operation, color=MUTED, wraplength=150).pack(
            side="bottom", anchor="w", padx=18, pady=24
        )
        self._build_library(self.views["Library"])
        self._build_spotify_search(self.views["Search Spotify"])
        self._build_activity(self.views["Activity"])
        self._build_settings(self.views["Settings"])
        self._show("Library")

    def _label(self, parent, text="", *, size=13, bold=False, color=INK, **kwargs):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, anchor="w",
                        font=("TkDefaultFont", size, "bold" if bold else "normal"), **kwargs)

    def _button(self, parent, text, command, *, primary=False, operation=False):
        button = ttk.Button(parent, text=text, command=command, cursor="pointinghand",
                            style="Primary.TButton" if primary else "Dashboard.TButton")
        if operation:
            self.operation_buttons.append(button)
        return button

    def _entry(self, parent, **kwargs):
        return tk.Entry(parent, bg=BG, fg=INK, insertbackground=INK, selectbackground=ACCENT,
                        selectforeground=BG, relief="flat", borderwidth=6,
                        highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT, **kwargs)

    def _heading(self, view, title, subtitle):
        self._label(view, title, size=30, bold=True).pack(anchor="w")
        self._label(view, subtitle, color=MUTED).pack(anchor="w", pady=(4, 20))

    def _show(self, name):
        for view in self.views.values():
            view.pack_forget()
        self.views[name].pack(fill="both", expand=True, padx=26, pady=22)
        for label, button in self.nav.items():
            button.configure(style="Primary.TButton" if label == name else "Dashboard.TButton")

    def _build_library(self, view):
        self._heading(view, "Playlist library", "Choose which Spotify playlists to sync to Music.")
        toolbar = tk.Frame(view, bg=BG)
        toolbar.pack(fill="x", pady=(0, 12))
        self._button(toolbar, "Sync now", lambda: self._sync(False), primary=True, operation=True).pack(side="left")
        self._button(toolbar, "Dry run", lambda: self._sync(True), operation=True).pack(side="left", padx=7)
        self._button(toolbar, "Load playlists", self._load_playlists, operation=True).pack(side="right")
        selection = tk.Frame(view, bg=BG)
        selection.pack(fill="x", pady=(0, 8))
        self.selection_count = tk.StringVar()
        self._label(selection, textvariable=self.selection_count, color=ACCENT, bold=True).pack(side="left")
        self._button(selection, "Save selection", self._save_selection).pack(side="right")

        card = tk.Frame(view, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True)
        filters = tk.Frame(card, bg=PANEL)
        filters.pack(fill="x", padx=12, pady=10)
        self.search = tk.StringVar()
        self.selected_only = tk.BooleanVar()
        self._label(filters, "Filter playlists").pack(side="left", padx=(0, 7))
        self._entry(filters, textvariable=self.search, width=18).pack(side="left", fill="x", expand=True)
        self._check(filters, "Selected only", self.selected_only).pack(side="left", padx=(8, 0))

        self.canvas = tk.Canvas(card, bg=PANEL, highlightthickness=0, height=150)
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 8))
        self.canvas.bind("<MouseWheel>", self._scroll_playlists)
        self.search.trace_add("write", self._filter_playlists)
        self.selected_only.trace_add("write", self._filter_playlists)

        links = tk.Frame(view, bg=BG)
        links.pack(fill="x", pady=(12, 0))
        self._label(links, "Spotify link").pack(side="left", padx=(0, 8))
        self.playlist_url = self._entry(links)
        self.playlist_url.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.playlist_url.bind("<Return>", lambda _: self._add_playlist_url())
        self._button(links, "Add playlist", self._add_playlist_url, operation=True).pack(side="right")

    def _check(self, parent, text, variable):
        return ttk.Checkbutton(parent, text=text, variable=variable, style="Dashboard.TCheckbutton")

    def _scroll_playlists(self, event):
        delta = event.delta
        if delta:
            steps = abs(delta) // 120 if abs(delta) >= 120 else abs(delta)
            self.canvas.yview_scroll(-steps if delta > 0 else steps, "units")
        return "break"

    def _build_spotify_search(self, view):
        self._heading(view, "Search Spotify", "Find playlists by name and add them to your sync library.")
        bar = tk.Frame(view, bg=BG)
        bar.pack(fill="x", pady=(0, 12))
        self.spotify_query = self._entry(bar)
        self.spotify_query.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.spotify_query.bind("<Return>", lambda _: self._search_spotify())
        self._button(bar, "Search", self._search_spotify, primary=True, operation=True).pack(side="right")

        self.search_status = tk.StringVar(value="Enter a playlist name to search Spotify.")
        self._label(view, textvariable=self.search_status, color=MUTED).pack(anchor="w", pady=(0, 8))
        results = tk.Frame(view, bg=PANEL)
        results.pack(fill="both", expand=True)
        self.spotify_results = ttk.Treeview(results, columns=("playlist", "owner"), show="headings",
                                            selectmode="browse", style="Dashboard.Treeview")
        self.spotify_results.heading("playlist", text="Playlist", anchor="w")
        self.spotify_results.heading("owner", text="Creator", anchor="w")
        self.spotify_results.column("playlist", width=345, minwidth=180)
        self.spotify_results.column("owner", width=170, minwidth=100)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.spotify_results.yview)
        self.spotify_results.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.spotify_results.pack(fill="both", expand=True)
        self.spotify_results.bind("<Return>", lambda _: self._add_search_result())
        self.spotify_results.bind("<Double-1>", self._activate_search_row)

        actions = tk.Frame(view, bg=BG)
        actions.pack(fill="x", pady=(12, 0))
        self._button(actions, "Add to sync", self._add_search_result, primary=True,
                     operation=True).pack(side="right")
        self._button(actions, "Open Spotify search", self._open_spotify_search).pack(side="right", padx=8)
        self.spotify_page = 0
        self.spotify_last_query = ""
        self.spotify_prev = self._button(actions, "Previous", lambda: self._page_spotify(-1))
        self.spotify_next = self._button(actions, "Next", lambda: self._page_spotify(1))
        for button in (self.spotify_prev, self.spotify_next):
            button.state(["disabled"])
            button.pack(side="left", padx=(0, 8))

    def _search_spotify(self):
        query = self.spotify_query.get().strip()
        if query:
            self._search_spotify_page(query, 0)
        else:
            self.spotify_query.focus_set()

    def _open_spotify_search(self):
        query = self.spotify_query.get().strip()
        if query:
            webbrowser.open(f"https://open.spotify.com/search/{quote(query, safe='')}/playlists")
        else:
            self.spotify_query.focus_set()

    def _page_spotify(self, step):
        self._search_spotify_page(self.spotify_last_query, self.spotify_page + step)

    def _search_spotify_page(self, query, page):
        if self.busy:
            return
        if not self._require_spotify_client():
            return
        client = SpotifyClient(self.client_id.get().strip(), self.redirect.get().strip())

        def finished(result):
            playlists, has_next = result
            self.spotify_last_query = query
            self.spotify_page = page
            self.spotify_prev.state(["!disabled" if page else "disabled"])
            self.spotify_next.state(["!disabled" if has_next else "disabled"])
            self.spotify_results.delete(*self.spotify_results.get_children())
            for item in playlists:
                self.spotify_results.insert("", "end", iid=item["id"], values=(item["name"], item["owner"]))
            status = f'Page {page + 1} for “{query}”' if playlists else f'No playlists found on this page for “{query}”.'
            self.search_status.set(status)

        self._run_operation("Searching Spotify…", lambda: client.search_playlists(query, page=page),
                            finished, "Spotify search")

    def _activate_search_row(self, event):
        if self.spotify_results.identify_row(event.y):
            self._add_search_result()

    def _add_search_result(self):
        selection = self.spotify_results.selection()
        if selection:
            self._add_playlist_url(selection[0], from_search=True)
        else:
            self.search_status.set("Select a playlist to add to your sync library.")

    def _build_activity(self, view):
        self._heading(view, "Activity", "Follow sync progress and resolve unmatched tracks.")
        self._button(view, "Fix unmatched tracks…", self._fix_unmatched, operation=True).pack(anchor="w", pady=(0, 12))
        log = tk.Frame(view, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        log.pack(fill="both", expand=True)
        self.status = tk.Text(log, height=8, wrap="word", bg=PANEL, fg=INK,
                              relief="flat", padx=12, pady=12, highlightthickness=0, state="disabled")
        scroll = ttk.Scrollbar(log, orient="vertical", command=self.status.yview)
        self.status.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.status.pack(fill="both", expand=True)

    def _build_settings(self, view):
        self._heading(view, "Settings", "Connect Spotify and manage automatic sync.")
        form = tk.Frame(view, bg=PANEL, padx=14, pady=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for index, (label, name) in enumerate((
            ("Spotify Client ID", "client_id"), ("Redirect URI", "redirect"),
            ("Staging folder", "output"),
        )):
            self._label(form, label).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=6)
            entry = self._entry(form)
            entry.grid(row=index, column=1, sticky="ew", pady=6)
            setattr(self, name, entry)
        self._button(form, "Choose…", self._choose_output).grid(row=2, column=2, padx=(7, 0))
        self._label(form, "Check every (hours)").grid(row=3, column=0, sticky="w", pady=6)
        self.schedule = ttk.Combobox(form, values=["1", "3", "6", "12", "24"], width=8, state="readonly")
        self.schedule.grid(row=3, column=1, sticky="w", pady=6)
        setup = tk.Frame(view, bg=BG)
        setup.pack(fill="x", pady=(10, 14))
        self._button(setup, "Sign in with Chrome", self._login, primary=True, operation=True).pack(side="left")
        self._button(setup, "Save settings", self._save).pack(side="left", padx=8)
        self._button(setup, "Spotify Developer Dashboard", lambda: webbrowser.open(
            "https://developer.spotify.com/dashboard"
        )).pack(side="left")
        self._label(view, "Register the Redirect URI in your Spotify app before signing in.",
                    color=MUTED, wraplength=440, justify="left").pack(anchor="w")
        schedule = tk.Frame(view, bg=BG)
        schedule.pack(fill="x", pady=(22, 0))
        self.schedule_status = tk.StringVar()
        self._label(schedule, textvariable=self.schedule_status, bold=True).pack(anchor="w", pady=(0, 8))
        self._button(schedule, "Install schedule", self._install_agent).pack(side="left")
        self._button(schedule, "Remove schedule", self._uninstall_agent).pack(side="left", padx=8)
        self._refresh_schedule()

    def _refresh_schedule(self):
        self.schedule_status.set("Automatic sync installed" if agent_installed() else "Automatic sync is off")

    def _load_into_form(self) -> None:
        cfg = self.config_data
        self.client_id.insert(0, cfg.spotify_client_id)
        self.redirect.insert(0, cfg.redirect_uri)
        self.output.insert(0, cfg.output_dir)
        self.schedule.set(str(cfg.schedule_hours))
        self._render_playlists([saved_playlist_entry(item) for item in cfg.playlists])

    def _bring_forward(self) -> None:
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _on_callback_exception(self, exc_type, exc, traceback_obj) -> None:
        get_logger().exception("Setup window error", exc_info=(exc_type, exc, traceback_obj))
        try:
            messagebox.showerror("Spotify → Music Sync", f"{exc_type.__name__}: {exc}")
        except Exception:
            pass

    def _log(self, message: str) -> None:
        self.status.configure(state="normal")
        self.status.insert(tk.END, message.rstrip() + "\n")
        self.status.configure(state="disabled")
        self.status.see(tk.END)

    def _form_config(self) -> AppConfig:
        cfg = load_config()
        cfg.spotify_client_id = self.client_id.get().strip()
        cfg.redirect_uri = self.redirect.get().strip()
        cfg.output_dir = self.output.get().strip()
        cfg.schedule_hours = int(self.schedule.get() or cfg.schedule_hours)
        selected = {key for key, var in self.playlist_vars.items() if var.get()}
        if self.remote_playlists:
            saved = {item.id: item for item in cfg.playlists}
            cfg.playlists = [
                PlaylistConfig(
                    id=item["id"],
                    name=item["name"],
                    url=item["url"],
                    enabled=item["id"] in selected,
                    music_name=saved[item["id"]].music_name if item["id"] in saved else None,
                    pinned=bool(item.get("pinned") or item.get("from_share_link")),
                )
                for item in self.remote_playlists
            ]
        else:
            for item in cfg.playlists:
                if item.id in self.playlist_vars:
                    item.enabled = bool(self.playlist_vars[item.id].get())
        return cfg

    def _save(self) -> AppConfig:
        cfg = self._form_config()
        save_config(cfg)
        self.config_data = cfg
        self._log(f"Saved {len(cfg.enabled_playlists())} playlist(s).")
        return cfg

    def _save_selection(self) -> None:
        cfg = self._save()
        if not self.busy:
            self.operation.set(f"Saved {len(cfg.enabled_playlists())} selected for sync")

    def _choose_output(self) -> None:
        chosen = filedialog.askdirectory()
        if chosen:
            self.output.delete(0, tk.END)
            self.output.insert(0, chosen)

    def _run_operation(self, status, work, finished, title):
        if self.busy:
            return
        self.busy = True
        for button in self.operation_buttons:
            button.state(["disabled"])
        self.operation.set(status)
        self._log(status)

        def complete(result=None, error=None):
            self.busy = False
            for button in self.operation_buttons:
                button.state(["!disabled"])
            self.operation.set("Ready" if error is None else f"{title} failed")
            if error is None:
                finished(result)
            else:
                self._log(f"{title}: {error}")
                dialog = messagebox.showinfo if isinstance(error, SyncLockError) else messagebox.showerror
                dialog(title, str(error), parent=self)

        def run():
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda error=exc: complete(error=error))
            else:
                self.after(0, lambda: complete(result=result))

        threading.Thread(target=run, daemon=True).start()

    def _login(self) -> None:
        if self.busy:
            return
        cfg = self._save()

        def work():
            client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
            client.get_access_token(interactive=True)
            return client.current_user()

        def finished(me):
            self.operation.set("Spotify connected")
            self._log(f"Signed in as {me.get('display_name') or me.get('id')}.")

        self._run_operation("Waiting for Spotify sign-in…", work, finished, "Spotify sign-in")

    def _require_spotify_client(self):
        if self.client_id.get().strip():
            return True
        self._show("Settings")
        self.client_id.focus_set()
        messagebox.showinfo("Connect Spotify", "Enter your Client ID and sign in with Chrome.", parent=self)
        return False

    def _load_playlists(self) -> None:
        if self.busy or not self._require_spotify_client():
            return
        cfg = self._save()

        def work():
            client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
            return client.list_playlists()

        def finished(playlists):
            self._render_playlists(merge_saved_playlists(playlists, self._form_config().playlists))

        self._run_operation("Loading playlists…", work, finished, "Playlist library")

    def _add_playlist_url(self, raw=None, *, from_search=False) -> None:
        if self.busy:
            return
        if raw is None:
            raw = self.playlist_url.get().strip()
        if not raw:
            self.playlist_url.focus_set()
            messagebox.showinfo("Playlist URL", "Paste a Spotify playlist link first.", parent=self)
            return
        if not self._require_spotify_client():
            return
        cfg = self._save()

        def work():
            playlist_id = parse_playlist_id(raw)
            client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
            return client.fetch_playlist(playlist_id)

        self._run_operation("Adding playlist…", work,
                            lambda data: self._playlist_url_added(data, from_search=from_search), "Add playlist")

    def _playlist_url_added(self, data: dict, *, from_search=False) -> None:
        playlist_id = str(data.get("id") or "")
        entry = {
            "id": playlist_id,
            "name": data.get("name") or playlist_id,
            "url": data.get("url") or playlist_share_url(playlist_id),
            "total": len(data.get("tracks") or []),
            "selected": True,
            "pinned": True,
            "owned": False,
            "made_for_you": bool(data.get("made_for_you")),
            "from_share_link": True,
        }
        current = [
            {**item, "selected": self.playlist_vars[item["id"]].get()}
            for item in self.remote_playlists
        ]
        if not current:
            current = [saved_playlist_entry(item) for item in self.config_data.playlists]
        replaced = False
        for index, item in enumerate(current):
            if item.get("id") == playlist_id:
                current[index] = {**item, **entry}
                replaced = True
                break
        if not replaced:
            liked = [item for item in current if item.get("id") == "liked-songs"]
            rest = [item for item in current if item.get("id") != "liked-songs"]
            current = liked + [entry] + rest
        if from_search:
            self.search_status.set(f'Added “{entry["name"]}” to your sync library.')
        else:
            self.playlist_url.delete(0, tk.END)
            self.search.set("")
        self._render_playlists(current)
        self._save()
        self._log(f"Added {entry['name']} ({entry['total']} tracks) to your sync library.")

    def _render_playlists(self, playlists: list[dict]) -> None:
        self.remote_playlists = playlists
        enabled = {item.id for item in self.config_data.enabled_playlists()}
        self.playlist_vars = {
            item["id"]: tk.BooleanVar(value=bool(item.get("selected", item["id"] in enabled)))
            for item in playlists
        }
        self._filter_playlists()
        if playlists:
            self._log(f"Loaded {len(playlists)} playlist(s).")

    def _filter_playlists(self, *_):
        for child in self.inner.winfo_children():
            child.destroy()
        query = self.search.get().strip().casefold()
        shown = 0
        for item in self.remote_playlists:
            var = self.playlist_vars[item["id"]]
            name = item.get("name") or item["id"]
            if query not in name.casefold() or (self.selected_only.get() and not var.get()):
                continue
            shown += 1
            row = tk.Frame(self.inner, bg=PANEL)
            row.pack(fill="x", padx=4)
            check = self._check(row, name, var)
            check.configure(command=self._selection_changed)
            check.pack(side="left", fill="x", expand=True, pady=5)
            total = item.get("total")
            details = [] if total in ("", None) else [f"{total} tracks"]
            if item.get("made_for_you"):
                details.append("Made for you")
            elif item.get("from_share_link") or item.get("pinned"):
                details.append("Share link")
            elif item.get("owned") is False:
                details.append("Followed")
            meta = self._label(row, " · ".join(details), color=MUTED, size=11)
            meta.pack(side="right", padx=10)
            divider = tk.Frame(self.inner, bg=BORDER, height=1)
            divider.pack(fill="x", padx=4)
            for widget in (row, check, meta, divider):
                widget.bind("<MouseWheel>", self._scroll_playlists)
        if not shown:
            message = "No playlists match these filters."
            if not self.remote_playlists:
                message = "Load your Spotify library or add a playlist link to get started."
            self._label(self.inner, message, color=MUTED, wraplength=400, justify="left").pack(
                anchor="w", padx=12, pady=24
            )
        self.canvas.yview_moveto(0)
        self._update_selection_count()

    def _selection_changed(self):
        if self.selected_only.get():
            self._filter_playlists()
        else:
            self._update_selection_count()

    def _update_selection_count(self):
        count = sum(var.get() for var in self.playlist_vars.values())
        self.selection_count.set(f"{count} of {len(self.playlist_vars)} selected for sync")

    def _sync(self, dry_run: bool) -> None:
        if self.busy:
            return
        if not any(var.get() for var in self.playlist_vars.values()):
            self._show("Library")
            messagebox.showinfo("Select playlists", "Choose at least one playlist to sync.", parent=self)
            return
        cfg = self._save()

        def work():
            with SyncLock():
                return SyncPipeline(
                    cfg,
                    on_progress=lambda msg: self.after(0, lambda: self._log(msg)),
                ).run(dry_run=dry_run)

        self._show("Activity")
        self._run_operation("Running preview…" if dry_run else "Syncing playlists…",
                            work, lambda report: self._sync_finished(report, dry_run=dry_run), "Sync")

    def _sync_finished(self, report, *, dry_run: bool) -> None:
        self.operation.set("Dry run finished" if dry_run else "Sync finished")
        self._log(report.summary())
        unmatched = list(getattr(report, "unmatched", None) or [])
        if dry_run or not unmatched:
            return
        self._log(f"{len(unmatched)} song(s) need a YouTube link. Opening Fix unmatched…")
        self._open_override_dialog(unmatched)

    def _fix_unmatched(self) -> None:
        self._open_override_dialog(None)

    def _open_override_dialog(self, unmatched) -> None:
        OverrideDialog(
            self,
            unmatched,
            on_saved_and_sync=lambda: self._sync(False),
        )

    def _install_agent(self) -> None:
        try:
            path = install_agent(self._save())
            self._log(f"Installed schedule: {path}")
            self._refresh_schedule()
        except Exception as exc:
            messagebox.showerror("Schedule", str(exc))

    def _uninstall_agent(self) -> None:
        uninstall_agent()
        self._refresh_schedule()
        self._log("Removed schedule." if not agent_installed() else "Tried to remove schedule.")
