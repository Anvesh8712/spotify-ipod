"""Dialog for pasting YouTube links when CSVMusic cannot match a track."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from spotify_ipod.csvmusic_bridge import load_overrides, load_unmatched, parse_youtube_id, save_overrides, save_unmatched


class OverrideDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        unmatched: list[dict[str, str]] | None = None,
        *,
        on_saved: Callable[[dict[str, str]], None] | None = None,
        on_saved_and_sync: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Fix unmatched songs")
        self.geometry("720x520")
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved
        self.on_saved_and_sync = on_saved_and_sync
        self.entries: dict[str, ttk.Entry] = {}
        self.items = list(unmatched) if unmatched is not None else load_unmatched()
        existing = load_overrides()

        pad = {"padx": 10, "pady": 6}
        ttk.Label(
            self,
            text="CSVMusic could not fetch these automatically. Paste a YouTube URL or 11-character video id, then Save & sync again.",
            wraplength=680,
        ).pack(anchor="w", **pad)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, **pad)
        canvas = tk.Canvas(body)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        if not self.items:
            ttk.Label(inner, text="No unmatched songs right now.").pack(anchor="w")
        for item in self.items:
            sp_id = str(item.get("sp_id") or "")
            if not sp_id:
                continue
            row = ttk.Frame(inner)
            row.pack(fill=tk.X, pady=4)
            label = f"{item.get('artists') or '?'} — {item.get('title') or sp_id}"
            reason = str(item.get("reason") or "")
            ttk.Label(row, text=label, wraplength=420).pack(anchor="w")
            if reason:
                ttk.Label(row, text=reason, foreground="#666666").pack(anchor="w")
            entry = ttk.Entry(row, width=70)
            entry.pack(fill=tk.X, pady=2)
            if sp_id in existing:
                entry.insert(0, existing[sp_id])
            self.entries[sp_id] = entry

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, **pad)
        ttk.Button(actions, text="Save overrides", command=lambda: self._save(sync=False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Save & sync again", command=lambda: self._save(sync=True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(100, self.lift)

    def _save(self, *, sync: bool) -> None:
        updates: dict[str, str] = {}
        invalid = False
        for sp_id, entry in self.entries.items():
            raw = entry.get().strip()
            if not raw:
                continue
            if not parse_youtube_id(raw):
                invalid = True
                break
            updates[sp_id] = raw
        if invalid:
            messagebox.showerror(
                "Fix unmatched",
                "Some YouTube links look invalid. Use a youtube.com / youtu.be URL or an 11-character video id.",
                parent=self,
            )
            return
        if not updates:
            messagebox.showinfo("Fix unmatched", "Paste at least one YouTube link first.", parent=self)
            return

        save_overrides(updates)
        remaining = [item for item in self.items if item.get("sp_id") not in updates]
        save_unmatched(remaining)
        self._log_parent(f"Saved {len(updates)} YouTube override(s).")
        if self.on_saved:
            self.on_saved(updates)
        if sync:
            self.destroy()
            if self.on_saved_and_sync:
                self.on_saved_and_sync()
            return
        messagebox.showinfo("Fix unmatched", f"Saved {len(updates)} override(s).", parent=self)

    def _log_parent(self, message: str) -> None:
        parent = self.master
        log = getattr(parent, "_log", None)
        if callable(log):
            log(message)
