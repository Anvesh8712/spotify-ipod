from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from spotify_ipod.paths import config_path, library_dir


DEFAULT_AAC_BITRATE = "256k"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHANNELS = 2
DEFAULT_SCHEDULE_HOURS = 6
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_CONFIDENCE_MIN = 0.6


@dataclass
class PlaylistConfig:
    id: str
    name: str
    url: str
    enabled: bool = True
    music_name: str | None = None
    pinned: bool = False

    @property
    def display_name(self) -> str:
        return (self.music_name or self.name or self.id).strip() or self.id


@dataclass
class AppConfig:
    spotify_client_id: str = ""
    redirect_uri: str = DEFAULT_REDIRECT_URI
    playlists: list[PlaylistConfig] = field(default_factory=list)
    output_dir: str = ""
    aac_bitrate: str = DEFAULT_AAC_BITRATE
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    schedule_hours: int = DEFAULT_SCHEDULE_HOURS
    confidence_min: float = DEFAULT_CONFIDENCE_MIN
    cookies_from_chrome: bool = True

    def enabled_playlists(self) -> list[PlaylistConfig]:
        return [item for item in self.playlists if item.enabled]

    def resolved_output_dir(self) -> str:
        return str(library_dir(self.output_dir or None))


def _playlist_from_dict(raw: dict[str, Any]) -> PlaylistConfig:
    return PlaylistConfig(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        url=str(raw.get("url") or ""),
        enabled=bool(raw.get("enabled", True)),
        music_name=raw.get("music_name") or None,
        pinned=bool(raw.get("pinned", False)),
    )


def config_from_dict(data: dict[str, Any] | None) -> AppConfig:
    raw = data or {}
    playlists = [_playlist_from_dict(item) for item in raw.get("playlists") or [] if isinstance(item, dict)]
    return AppConfig(
        spotify_client_id=str(raw.get("spotify_client_id") or ""),
        redirect_uri=str(raw.get("redirect_uri") or DEFAULT_REDIRECT_URI),
        playlists=playlists,
        output_dir=str(raw.get("output_dir") or ""),
        aac_bitrate=str(raw.get("aac_bitrate") or DEFAULT_AAC_BITRATE),
        sample_rate=int(raw.get("sample_rate") or DEFAULT_SAMPLE_RATE),
        channels=int(raw.get("channels") or DEFAULT_CHANNELS),
        schedule_hours=int(raw.get("schedule_hours") or DEFAULT_SCHEDULE_HOURS),
        confidence_min=float(raw.get("confidence_min") or DEFAULT_CONFIDENCE_MIN),
        cookies_from_chrome=bool(raw.get("cookies_from_chrome", True)),
    )


def load_config(path=None) -> AppConfig:
    target = path or config_path()
    if not target.exists():
        return AppConfig()
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    if not isinstance(data, dict):
        return AppConfig()
    return config_from_dict(data)


def save_config(config: AppConfig, path=None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    tmp.replace(target)
