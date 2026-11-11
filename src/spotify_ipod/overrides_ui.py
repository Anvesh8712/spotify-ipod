"""Dialog for choosing YouTube matches when CSVMusic is not confident."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from spotify_ipod.csvmusic_bridge import load_overrides, load_unmatched, parse_youtube_id, save_overrides, save_unmatched

CUSTOM = "__custom__"


class OverrideDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        unmatched: list[dict[str, Any]] | None = None,
        *,
        on_saved: Callable[[dict[str, str]], None] | None = None,
        on_saved_and_sync: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Fix unmatched songs")
        self.geometry("760x560")
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved
        self.on_saved_and_sync = on_saved_and_sync
        self.choice_vars: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        self.items = list(unmatched) if unmatched is not None else load_unmatched()
        existing = load_overrides()

        pad = {"padx": 10, "pady": 6}
        ttk.Label(
            self,
            text=(
                "These songs need a YouTube pick. Choose a suggested match or paste your own "
                "YouTube URL / 11-character video id, then Save & sync again."
            ),
            wraplength=720,
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
            row = ttk.LabelFrame(inner, text=f"{item.get('artists') or '?'} — {item.get('title') or sp_id}")
            row.pack(fill=tk.X, pady=8, padx=2)

            reason = str(item.get("reason") or "")
            confidence = float(item.get("confidence") or 0)
            meta = reason
            if confidence > 0 and "confidence" not in reason.lower():
                meta = f"{reason}  (best score {confidence:.2f})" if reason else f"best score {confidence:.2f}"
            if meta:
                ttk.Label(row, text=meta, foreground="#555555", wraplength=680).pack(anchor="w", padx=8, pady=(4, 2))

            candidates = item.get("candidates") or []
            if not isinstance(candidates, list):
                candidates = []

            preset = existing.get(sp_id, "")
            initial = preset or (str(candidates[0].get("video_id") or "") if candidates else CUSTOM)
            if not initial:
                initial = CUSTOM
            choice = tk.StringVar(value=initial)
            self.choice_vars[sp_id] = choice

            for cand in candidates:
                video_id = str(cand.get("video_id") or "")
                if not video_id:
                    continue
                score = float(cand.get("score") or 0)
                label = f"{score:.2f}  {cand.get('title') or video_id}"
                artists = str(cand.get("artists") or "")
                if artists:
                    label = f"{label} — {artists}"
                ttk.Radiobutton(row, text=label, variable=choice, value=video_id).pack(anchor="w", padx=12)

            ttk.Radiobutton(row, text="Custom YouTube URL / video id", variable=choice, value=CUSTOM).pack(
                anchor="w", padx=12, pady=(4, 0)
            )
            entry = ttk.Entry(row, width=72)
            entry.pack(fill=tk.X, padx=12, pady=(2, 8))
            if preset and (not candidates or all(str(c.get("video_id")) != preset for c in candidates)):
                choice.set(CUSTOM)
                entry.insert(0, preset)
            self.entries[sp_id] = entry

            def _on_custom_focus(_event=None, sp=sp_id):
                self.choice_vars[sp].set(CUSTOM)

            entry.bind("<FocusIn>", _on_custom_focus)

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, **pad)
        ttk.Button(actions, text="Save overrides", command=lambda: self._save(sync=False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Save & sync again", command=lambda: self._save(sync=True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(100, self.lift)

    def _selected_video_id(self, sp_id: str) -> str:
        choice = self.choice_vars[sp_id].get().strip()
        if choice and choice != CUSTOM:
            return parse_youtube_id(choice) or choice
        raw = self.entries[sp_id].get().strip()
        return parse_youtube_id(raw)

    def _save(self, *, sync: bool) -> None:
        updates: dict[str, str] = {}
        invalid = False
        for sp_id in self.choice_vars:
            choice = self.choice_vars[sp_id].get().strip()
            if choice == CUSTOM and not self.entries[sp_id].get().strip():
                continue
            if choice and choice != CUSTOM:
                updates[sp_id] = choice
                continue
            video_id = self._selected_video_id(sp_id)
            if not video_id:
                if self.entries[sp_id].get().strip():
                    invalid = True
                    break
                continue
            updates[sp_id] = video_id

        if invalid:
            messagebox.showerror(
                "Fix unmatched",
                "Some YouTube links look invalid. Use a youtube.com / youtu.be URL or an 11-character video id.",
                parent=self,
            )
            return
        if not updates:
            messagebox.showinfo(
                "Fix unmatched",
                "Pick a suggested match or paste at least one YouTube link first.",
                parent=self,
            )
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
