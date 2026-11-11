from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spotify_ipod.paths import state_path


@dataclass
class TrackRecord:
    sp_id: str
    title: str
    artists: str
    album: str = ""
    file_path: str = ""
    music_persistent_id: str = ""
    video_id: str = ""
    duration_ms: int = 0


@dataclass
class PlaylistRecord:
    id: str
    name: str
    snapshot: list[str] = field(default_factory=list)
    music_persistent_id: str = ""
    music_name: str = ""


@dataclass
class AppState:
    tracks: dict[str, TrackRecord] = field(default_factory=dict)
    playlists: dict[str, PlaylistRecord] = field(default_factory=dict)

    def track(self, sp_id: str) -> TrackRecord | None:
        return self.tracks.get(sp_id)

    def upsert_track(self, record: TrackRecord) -> None:
        self.tracks[record.sp_id] = record

    def drop_track(self, sp_id: str) -> TrackRecord | None:
        return self.tracks.pop(sp_id, None)


def _track_from_dict(raw: dict[str, Any]) -> TrackRecord:
    return TrackRecord(
        sp_id=str(raw.get("sp_id") or ""),
        title=str(raw.get("title") or ""),
        artists=str(raw.get("artists") or ""),
        album=str(raw.get("album") or ""),
        file_path=str(raw.get("file_path") or ""),
        music_persistent_id=str(raw.get("music_persistent_id") or ""),
        video_id=str(raw.get("video_id") or ""),
        duration_ms=int(raw.get("duration_ms") or 0),
    )


def _playlist_from_dict(raw: dict[str, Any], fallback_id: str) -> PlaylistRecord:
    return PlaylistRecord(
        id=str(raw.get("id") or fallback_id),
        name=str(raw.get("name") or ""),
        snapshot=[str(item) for item in raw.get("snapshot") or []],
        music_persistent_id=str(raw.get("music_persistent_id") or ""),
        music_name=str(raw.get("music_name") or ""),
    )


def state_from_dict(data: dict[str, Any] | None) -> AppState:
    raw = data or {}
    tracks = {}
    for key, value in (raw.get("tracks") or {}).items():
        if isinstance(value, dict):
            record = _track_from_dict(value)
            tracks[str(key)] = record
    playlists = {}
    for key, value in (raw.get("playlists") or {}).items():
        if isinstance(value, dict):
            playlists[str(key)] = _playlist_from_dict(value, str(key))
    return AppState(tracks=tracks, playlists=playlists)


def load_state(path: Path | None = None) -> AppState:
    target = path or state_path()
    if not target.exists():
        return AppState()
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return AppState()
    if not isinstance(data, dict):
        return AppState()
    return state_from_dict(data)


def save_state(state: AppState, path: Path | None = None) -> None:
    """Atomically persist state so a crash cannot leave a half-written file."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tracks": {key: asdict(value) for key, value in state.tracks.items()},
        "playlists": {key: asdict(value) for key, value in state.playlists.items()},
    }
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    os.replace(tmp, target)


def track_key(track: dict[str, Any]) -> str:
    sp_id = str(track.get("sp_id") or "").strip()
    if sp_id:
        return sp_id
    title = str(track.get("title") or "").strip().casefold()
    artists = str(track.get("artists") or "").strip().casefold()
    duration = int(track.get("duration_ms") or 0)
    return f"local:{title}|{artists}|{duration}"


def used_track_ids(playlists: dict[str, list[str]]) -> set[str]:
    used: set[str] = set()
    for snapshot in playlists.values():
        used.update(snapshot)
    return used


def orphan_track_ids(state: AppState, current_playlists: dict[str, list[str]]) -> list[str]:
    used = used_track_ids(current_playlists)
    return [sp_id for sp_id in state.tracks if sp_id not in used]


def playlist_changed(old: list[str], new: list[str]) -> bool:
    return list(old) != list(new)
