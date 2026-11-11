from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from spotify_ipod.config import AppConfig, PlaylistConfig, load_config, save_config
from spotify_ipod.lock import SyncLock, SyncLockError
from spotify_ipod.logging_setup import get_logger, setup_logging
from spotify_ipod.pipeline import SyncPipeline
from spotify_ipod.scheduler import agent_installed, install_agent, uninstall_agent
from spotify_ipod.overrides_ui import OverrideDialog
from spotify_ipod.spotify import SpotifyClient, SpotifyError, merge_saved_playlists, parse_playlist_id, playlist_share_url, saved_playlist_entry


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
        self.geometry("780x700")
        self.config_data = load_config()
        self.playlist_vars: dict[str, tk.BooleanVar] = {}
        self.remote_playlists: list[dict] = []
        self.report_callback_exception = self._on_callback_exception
        self._build()
        self._load_into_form()
        self.after(200, self._bring_forward)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        ttk.Label(frm, text="Spotify Client ID").grid(row=0, column=0, sticky="w")
        self.client_id = ttk.Entry(frm, width=52)
        self.client_id.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="Redirect URI").grid(row=1, column=0, sticky="w")
        self.redirect = ttk.Entry(frm, width=52)
        self.redirect.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="Download staging folder").grid(row=2, column=0, sticky="w")
        self.output = ttk.Entry(frm, width=42)
        self.output.grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Choose…", command=self._choose_output).grid(row=2, column=2, sticky="e")

        ttk.Label(frm, text="Check every (hours)").grid(row=3, column=0, sticky="w")
        self.schedule = ttk.Combobox(frm, values=["1", "3", "6", "12", "24"], width=8, state="readonly")
        self.schedule.grid(row=3, column=1, sticky="w", **pad)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ttk.Button(btns, text="Sign in with Chrome", command=self._login).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Load playlists", command=self._load_playlists).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.LEFT, padx=4)

        ttk.Label(frm, text="Playlist URL").grid(row=5, column=0, sticky="w")
        self.playlist_url = ttk.Entry(frm, width=42)
        self.playlist_url.grid(row=5, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Add URL", command=self._add_playlist_url).grid(row=5, column=2, sticky="e")

        ttk.Label(frm, text="Playlists, including Liked Songs and share links").grid(row=6, column=0, columnspan=3, sticky="w")
        self.list_frame = ttk.Frame(frm)
        self.list_frame.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.canvas = tk.Canvas(self.list_frame, height=220)
        scroll = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        actions = ttk.Frame(frm)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        ttk.Button(actions, text="Sync now", command=lambda: self._sync(False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Dry run", command=lambda: self._sync(True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Fix unmatched…", command=self._fix_unmatched).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Install schedule", command=self._install_agent).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Remove schedule", command=self._uninstall_agent).pack(side=tk.LEFT, padx=4)

        self.status = tk.Text(frm, height=10, wrap=tk.WORD)
        self.status.grid(row=9, column=0, columnspan=3, sticky="nsew", **pad)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(7, weight=1)
        frm.rowconfigure(9, weight=1)
        self._log(
            "Create a Spotify app at https://developer.spotify.com/dashboard\n"
            "Add the Redirect URI exactly, copy the Client ID, then Sign in with Chrome.\n"
            "Downloads use CSVMusic 1.7.7; Music converts with AAC Encoder.\n"
            "If songs are low-confidence or unmatched, click Fix unmatched… to pick a YouTube candidate or paste a link."
        )

    def _load_into_form(self) -> None:
        cfg = self.config_data
        self.client_id.insert(0, cfg.spotify_client_id)
        self.redirect.insert(0, cfg.redirect_uri)
        self.output.insert(0, cfg.output_dir)
        self.schedule.set(str(cfg.schedule_hours))
        if cfg.playlists:
            self._render_playlists(
                [
                    saved_playlist_entry(item)
                    for item in cfg.playlists
                ]
            )

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
        self.status.insert(tk.END, message.rstrip() + "\n")
        self.status.see(tk.END)

    def _form_config(self) -> AppConfig:
        cfg = load_config()
        cfg.spotify_client_id = self.client_id.get().strip()
        cfg.redirect_uri = self.redirect.get().strip()
        cfg.output_dir = self.output.get().strip()
        cfg.schedule_hours = int(self.schedule.get() or cfg.schedule_hours)
        selected = {key for key, var in self.playlist_vars.items() if var.get()}
        if self.remote_playlists:
            cfg.playlists = [
                PlaylistConfig(
                    id=item["id"],
                    name=item["name"],
                    url=item["url"],
                    enabled=item["id"] in selected,
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

    def _choose_output(self) -> None:
        chosen = filedialog.askdirectory()
        if chosen:
            self.output.delete(0, tk.END)
            self.output.insert(0, chosen)

    def _login(self) -> None:
        cfg = self._save()

        def work() -> None:
            try:
                client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
                client.get_access_token(interactive=True)
                me = client.current_user()
                self.after(0, lambda: self._log(f"Signed in as {me.get('display_name') or me.get('id')}"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Spotify", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self._log("Opening Google Chrome for Spotify authorization…")

    def _load_playlists(self) -> None:
        cfg = self._save()

        def work() -> None:
            try:
                client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
                playlists = merge_saved_playlists(client.list_playlists(), cfg.playlists)
                self.after(0, lambda: self._render_playlists(playlists))
            except SpotifyError as exc:
                self.after(0, lambda: messagebox.showerror("Spotify", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self._log("Loading playlists from Spotify…")

    def _add_playlist_url(self) -> None:
        raw = self.playlist_url.get().strip()
        if not raw:
            messagebox.showinfo("Playlist URL", "Paste a Spotify playlist link first.")
            return
        cfg = self._save()

        def work() -> None:
            try:
                playlist_id = parse_playlist_id(raw)
                client = SpotifyClient(cfg.spotify_client_id, cfg.redirect_uri)
                data = client.fetch_playlist(playlist_id)
                self.after(0, lambda: self._playlist_url_added(data))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Playlist URL", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self._log(f"Adding playlist from {raw}…")

    def _playlist_url_added(self, data: dict) -> None:
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
        current = list(self.remote_playlists)
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
        self.playlist_url.delete(0, tk.END)
        self._render_playlists(current)
        self._save()
        source = "share link" if data.get("from_share_link") else "Spotify"
        self._log(f"Watching {entry['name']} ({entry['total']} tracks) from {source}. It stays on the list after Load playlists.")

    def _render_playlists(self, playlists: list[dict]) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        self.playlist_vars = {}
        self.remote_playlists = playlists
        enabled = {item.id for item in self.config_data.enabled_playlists()}
        for playlist in playlists:
            pid = playlist["id"]
            selected = bool(playlist.get("selected", pid in enabled))
            var = tk.BooleanVar(value=selected)
            self.playlist_vars[pid] = var
            total = playlist.get("total")
            count = "?" if total in ("", None) else total
            extra = ""
            if playlist.get("made_for_you"):
                extra = " · Made For You"
            elif playlist.get("from_share_link") or playlist.get("pinned"):
                extra = " · share link"
            elif playlist.get("owned") is False:
                extra = " · followed"
            label = f"{playlist.get('name')}  ({count} tracks){extra}"
            ttk.Checkbutton(self.inner, text=label, variable=var).pack(anchor="w")
        self._log(f"Loaded {len(playlists)} playlist(s). Check the ones to follow.")
        if not any(item.get("made_for_you") or item.get("from_share_link") for item in playlists):
            self._log(
                "Mixes like Chill Mix are missing from Spotify's library API. Paste a share URL above and click Add URL."
            )

    def _sync(self, dry_run: bool) -> None:
        cfg = self._save()

        def work() -> None:
            try:
                with SyncLock():
                    report = SyncPipeline(
                        cfg,
                        on_progress=lambda msg: self.after(0, lambda m=msg: self._log(m)),
                    ).run(dry_run=dry_run)
                self.after(0, lambda: self._sync_finished(report, dry_run=dry_run))
            except SyncLockError as exc:
                msg = str(exc)
                self.after(0, lambda m=msg: messagebox.showinfo("Sync", m))
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda m=msg: messagebox.showerror("Sync", m))

        threading.Thread(target=work, daemon=True).start()
        self._log("Dry run…" if dry_run else "Syncing…")

    def _sync_finished(self, report, *, dry_run: bool) -> None:
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
        except Exception as exc:
            messagebox.showerror("Schedule", str(exc))

    def _uninstall_agent(self) -> None:
        uninstall_agent()
        self._log("Removed schedule." if not agent_installed() else "Tried to remove schedule.")
