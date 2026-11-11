"""Download audio via CSVMusic 1.7.7 — we do not reimplement matching/download."""

from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from spotify_ipod.paths import support_dir, unmatched_path
from spotify_ipod.state import track_key

ProgressFn = Callable[[str], None]


@dataclass
class BatchDownloadResult:
    files: dict[str, Path] = field(default_factory=dict)
    failed: list[dict[str, Any]] = field(default_factory=list)


class DownloadError(RuntimeError):
    pass


def _log():
    from spotify_ipod.logging_setup import get_logger

    return get_logger()


def _require_csvmusic() -> None:
    try:
        import csvmusic  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise DownloadError(
            "CSVMusic is not installed. Install the v1.7.7 wheel from "
            "https://github.com/angall1/CSVMusic/releases/tag/v1.7.7"
        ) from exc


def overrides_path() -> Path:
    return support_dir() / "overrides.json"


def load_overrides(path: Path | None = None) -> dict[str, str]:
    """
    Manual YouTube overrides when CSVMusic cannot match a track.

    File: ~/Library/Application Support/spotify-ipod/overrides.json
    Format: { "<spotify_track_id>": "<youtube_video_id_or_url>" }
    """
    target = path or overrides_path()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        video_id = parse_youtube_id(str(value or ""))
        if key and video_id:
            out[str(key)] = video_id
    return out


def save_overrides(updates: dict[str, str], path: Path | None = None) -> dict[str, str]:
    """Merge YouTube overrides and write overrides.json."""
    target = path or overrides_path()
    current = load_overrides(target)
    for key, value in updates.items():
        video_id = parse_youtube_id(str(value or ""))
        if not key or not video_id:
            continue
        current[str(key)] = video_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def _clean_candidates(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for cand in raw:
        if not isinstance(cand, dict):
            continue
        video_id = str(cand.get("video_id") or "").strip()
        if not video_id:
            continue
        rows.append(
            {
                "video_id": video_id,
                "title": str(cand.get("title") or ""),
                "artists": str(cand.get("artists") or ""),
                "score": float(cand.get("score") or 0),
            }
        )
    return rows


def load_unmatched(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or unmatched_path()
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sp_id = str(item.get("sp_id") or "").strip()
        if not sp_id:
            continue
        out.append(
            {
                "sp_id": sp_id,
                "title": str(item.get("title") or ""),
                "artists": str(item.get("artists") or ""),
                "reason": str(item.get("reason") or "unmatched"),
                "confidence": float(item.get("confidence") or 0),
                "candidates": _clean_candidates(item.get("candidates")),
            }
        )
    return out


def save_unmatched(items: list[dict[str, Any]], path: Path | None = None) -> None:
    target = path or unmatched_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    seen: set[str] = set()
    for item in items:
        sp_id = str(item.get("sp_id") or "").strip()
        if not sp_id or sp_id in seen:
            continue
        seen.add(sp_id)
        payload.append(
            {
                "sp_id": sp_id,
                "title": str(item.get("title") or ""),
                "artists": str(item.get("artists") or ""),
                "reason": str(item.get("reason") or "unmatched"),
                "confidence": float(item.get("confidence") or 0),
                "candidates": _clean_candidates(item.get("candidates")),
            }
        )
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_youtube_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[\w-]{11}", text):
        return text
    patterns = [
        r"(?:v=|/embed/|/shorts/|youtu\.be/)([\w-]{11})",
        r"([\w-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _sanitize_like_csvmusic(name: str) -> str:
    """Best-effort filename stem matching CSVMusic's sanitize_name."""
    try:
        from csvmusic.core.downloader import sanitize_name

        return sanitize_name(name)
    except Exception:
        text = unicodedata.normalize("NFC", name or "")
        text = re.sub(r'[\\/:*?"<>|]', "-", text)
        text = re.sub(r"[\x00-\x1f]+", "", text)
        text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
        return text or "_"


def write_playlist_csv(tracks: list[dict[str, Any]], playlist_name: str, path: Path) -> Path:
    """Write a TuneMyMusic-style CSV that CSVMusic accepts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Track name",
        "Artist name",
        "Album",
        "Playlist name",
        "Duration (ms)",
        "Spotify - id",
        "ISRC",
        "Cover URL",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for track in tracks:
            writer.writerow(
                {
                    "Track name": str(track.get("title") or ""),
                    "Artist name": str(track.get("artists") or ""),
                    "Album": str(track.get("album") or ""),
                    "Playlist name": playlist_name,
                    "Duration (ms)": int(track.get("duration_ms") or 0),
                    "Spotify - id": str(track.get("sp_id") or ""),
                    "ISRC": str(track.get("isrc") or ""),
                    "Cover URL": str(track.get("cover_url") or ""),
                }
            )
    return path


def expected_filename(track: dict[str, Any]) -> str:
    base = f"{track.get('artists', '')} - {track.get('title', '')}"
    return f"{_sanitize_like_csvmusic(base)}.m4a"


def _find_downloaded_file(dest_dir: Path, track: dict[str, Any]) -> Path | None:
    expected = expected_filename(track)
    direct = dest_dir / expected
    if direct.exists():
        return direct
    needle = _sanitize_like_csvmusic(f"{track.get('artists', '')} - {track.get('title', '')}").casefold()
    for path in dest_dir.glob("*.m4a"):
        if path.stem.casefold() == needle:
            return path
    return None


def _to_csvmusic_track(track: dict[str, Any], playlist_name: str, position: int) -> dict[str, Any]:
    return {
        "title": str(track.get("title") or "").strip(),
        "artists": str(track.get("artists") or "").strip(),
        "album": str(track.get("album") or "").strip(),
        "playlist": playlist_name,
        "isrc": (str(track.get("isrc") or "").strip() or None),
        "sp_id": str(track.get("sp_id") or "").strip() or None,
        "duration_ms": int(track.get("duration_ms") or 0),
        "year": None,
        "cover_url": track.get("cover_url") or None,
        "track_no": position,
        "disc_no": 1,
    }


def _format_artists(value: Any) -> str:
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                names.append(str(item.get("name") or ""))
            else:
                names.append(str(item))
        return ", ".join(n for n in names if n)
    return str(value or "")


def _candidate_rows(options: list[dict[str, Any]] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for opt in (options or [])[:limit]:
        video_id = str(opt.get("videoId") or "").strip()
        if not video_id:
            continue
        rows.append(
            {
                "video_id": video_id,
                "title": str(opt.get("title") or ""),
                "artists": _format_artists(opt.get("artists"))
                or str(opt.get("author") or opt.get("channel") or ""),
                "score": float(opt.get("score") or 0),
            }
        )
    return rows


def _fail_item(
    track: dict[str, Any],
    reason: str,
    *,
    confidence: float = 0.0,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "sp_id": track_key(track),
        "title": str(track.get("title") or ""),
        "artists": str(track.get("artists") or ""),
        "reason": reason,
        "confidence": float(confidence or 0),
        "candidates": list(candidates or []),
    }


def _download_with_csvmusic_core(
    tracks: list[dict[str, Any]],
    out_dir: Path,
    playlist_name: str,
    *,
    on_progress: ProgressFn,
    overrides: dict[str, str],
) -> BatchDownloadResult:
    """Match + download using CSVMusic's own modules, with live progress."""
    _require_csvmusic()
    from csvmusic.core.downloader import download_m4a, sanitize_name, tag_file, yt_thumbnail_bytes
    from csvmusic.core.ytmusic_match import RATE_LIMIT_S, find_best
    from ytmusicapi import YTMusic

    safe_playlist = sanitize_name(playlist_name) or "Playlist"
    dest_dir = out_dir / safe_playlist
    dest_dir.mkdir(parents=True, exist_ok=True)

    total = len(tracks)
    on_progress(f"CSVMusic: matching & downloading {total} track(s) for {playlist_name}…")
    yt = YTMusic()
    result = BatchDownloadResult()

    for index, track in enumerate(tracks, start=1):
        key = track_key(track)
        label = f"{track.get('artists')} - {track.get('title')}"
        on_progress(f"CSVMusic [{index}/{total}] {label}")

        existing = _find_downloaded_file(dest_dir, track)
        if existing and existing.exists() and existing.stat().st_size > 0:
            on_progress(f"  already on disk → {existing.name}")
            result.files[key] = existing
            continue

        video_id = overrides.get(key, "")
        csv_track = _to_csvmusic_track(track, playlist_name, index)
        if video_id:
            on_progress(f"  manual override → {video_id}")
        else:
            try:
                match, conf, options = find_best(yt, csv_track)
            except Exception as exc:
                reason = f"match error: {exc}"
                on_progress(f"  {reason} — open Fix unmatched to pick a YouTube link")
                _log().warning("CSVMusic match failed for %s: %s", label, exc)
                result.failed.append(_fail_item(track, reason))
                time.sleep(RATE_LIMIT_S)
                continue
            time.sleep(RATE_LIMIT_S)
            candidates = _candidate_rows(options)
            if match is None:
                if candidates:
                    reason = f"low confidence ({conf:.2f}) — pick a candidate or paste a YouTube link"
                else:
                    reason = "no confident YouTube Music match"
                on_progress(f"  {reason}")
                result.failed.append(
                    _fail_item(track, reason, confidence=float(conf or 0), candidates=candidates)
                )
                continue
            video_id = str(match.get("videoId") or "")
            if not video_id:
                reason = "match missing video id"
                on_progress(f"  {reason} — skipped")
                result.failed.append(
                    _fail_item(track, reason, confidence=float(conf or 0), candidates=candidates)
                )
                continue
            on_progress(f"  matched ({conf:.2f}) → downloading…")

        base = f"{csv_track['artists']} - {csv_track['title']}"
        try:
            path = download_m4a(video_id, dest_dir, base)
            cover = None
            try:
                cover = yt_thumbnail_bytes(video_id)
            except Exception:
                cover = None
            try:
                tag_file(path, csv_track, cover)
            except Exception as tag_exc:
                _log().warning("CSVMusic tag failed for %s: %s", label, tag_exc)
            on_progress(f"  downloaded → {path.name}")
            result.files[key] = path
        except Exception as exc:
            reason = f"download failed: {exc}"
            on_progress(f"  {reason}")
            _log().warning("CSVMusic download failed for %s: %s", label, exc)
            result.failed.append(_fail_item(track, reason))

    on_progress(f"CSVMusic done for {playlist_name}: {len(result.files)}/{total} downloaded")
    return result


def download_tracks(
    tracks: list[dict[str, Any]],
    out_dir: Path,
    playlist_name: str,
    *,
    runner: Callable[[Path, Path, str], int] | None = None,
    on_progress: ProgressFn | None = None,
    overrides: dict[str, str] | None = None,
) -> BatchDownloadResult:
    """
    Download missing tracks with CSVMusic.

    Returns files + failed entries (for the Fix unmatched UI).
    """
    if not tracks:
        return BatchDownloadResult()

    progress = on_progress or (lambda _msg: None)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    override_map = overrides if overrides is not None else load_overrides()

    if runner is not None:
        csv_path = out_dir / f"{_sanitize_like_csvmusic(playlist_name) or 'playlist'}.csv"
        write_playlist_csv(tracks, playlist_name, csv_path)
        code = runner(csv_path, out_dir, playlist_name)
        if code not in (0, None):
            _log().warning("CSVMusic exited with code %s for %s", code, playlist_name)
        playlist_dir = out_dir / (_sanitize_like_csvmusic(playlist_name) or "Playlist")
        search_dirs = [playlist_dir, out_dir] if playlist_dir.exists() else [out_dir]
        result = BatchDownloadResult()
        for track in tracks:
            key = track_key(track)
            path: Path | None = None
            for folder in search_dirs:
                path = _find_downloaded_file(folder, track)
                if path:
                    break
            if path:
                result.files[key] = path
            else:
                result.failed.append(_fail_item(track, "runner did not produce a file"))
        return result

    return _download_with_csvmusic_core(
        tracks,
        out_dir,
        playlist_name,
        on_progress=progress,
        overrides=override_map,
    )
