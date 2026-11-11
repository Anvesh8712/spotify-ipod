from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable

import requests
from yt_dlp import YoutubeDL

from spotify_ipod.audio import sanitize_name, tag_aac, transcode_to_aac
from spotify_ipod.config import AppConfig
from spotify_ipod.logging_setup import get_logger
from spotify_ipod.match import RATE_LIMIT_S, find_best
from spotify_ipod.paths import staging_dir

try:
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover
    YTMusic = None  # type: ignore[assignment]


class DownloadError(RuntimeError):
    pass


def cover_bytes(url: str | None) -> bytes | None:
    if not url or not str(url).lower().startswith(("https://", "http://")):
        return None
    try:
        response = requests.get(url, timeout=12)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    content_type = str(response.headers.get("content-type") or "").lower()
    if not content_type.startswith("image/"):
        return None
    data = response.content
    if not data or len(data) > 10 * 1024 * 1024:
        return None
    return data


def output_filename(track: dict[str, Any]) -> str:
    base = f"{track.get('artists', '')} - {track.get('title', '')}"
    return f"{sanitize_name(base)}.m4a"


def _ytdlp_opts(dest_dir: Path, *, cookies_from_chrome: bool) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "retries": 3,
    }
    deno = shutil.which("deno") or ("/opt/homebrew/bin/deno" if Path("/opt/homebrew/bin/deno").exists() else None)
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    opts["remote_components"] = ["ejs:github"]
    if cookies_from_chrome:
        opts["cookiesfrombrowser"] = ("chrome",)
    return opts


def download_youtube_audio(video_id: str, dest_dir: Path, *, cookies_from_chrome: bool = True) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    last_error: Exception | None = None
    attempts = []
    if cookies_from_chrome:
        attempts.append(True)
    attempts.append(False)
    seen: set[bool] = set()
    for use_cookies in attempts:
        if use_cookies in seen:
            continue
        seen.add(use_cookies)
        try:
            with YoutubeDL(_ytdlp_opts(dest_dir, cookies_from_chrome=use_cookies)) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as exc:
            last_error = exc
            info = None
    else:
        raise DownloadError(f"Could not download {video_id}: {last_error}") from last_error
    if not isinstance(info, dict):
        raise DownloadError(f"yt-dlp did not return info for {video_id}.")
    requested = info.get("requested_downloads") or []
    filepath = None
    if requested and isinstance(requested[0], dict):
        filepath = requested[0].get("filepath")
    filepath = filepath or info.get("filepath") or info.get("_filename")
    if not filepath:
        matches = sorted(dest_dir.glob(f"{video_id}.*"))
        if not matches:
            raise DownloadError(f"Download finished but no file was found for {video_id}.")
        return matches[0]
    return Path(filepath)


def prepare_track_file(
    track: dict[str, Any],
    dest_dir: Path,
    config: AppConfig,
    *,
    yt: Any | None = None,
    match_fn: Callable[..., tuple[dict[str, Any] | None, float, list]] | None = None,
    download_fn: Callable[..., Path] | None = None,
    transcode_fn: Callable[..., Path] | None = None,
    tag_fn: Callable[..., None] | None = None,
    cover_fn: Callable[[str | None], bytes | None] | None = None,
) -> dict[str, Any]:
    logger = get_logger()
    matcher = match_fn or find_best
    client = yt
    if client is None and match_fn is None:
        if YTMusic is None:
            raise DownloadError("ytmusicapi is not installed.")
        client = YTMusic()
    match, confidence, options = matcher(client, track, confidence_min=config.confidence_min)
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in ([match] if match else []) + list(options or []):
        video_id = str(candidate.get("videoId") or "")
        if not video_id or video_id in seen_ids:
            continue
        seen_ids.add(video_id)
        candidates.append(candidate)
    if not candidates:
        raise DownloadError(f"No confident YouTube match for {track.get('artists')} - {track.get('title')}")
    downloader = download_fn or download_youtube_audio
    converter = transcode_fn or transcode_to_aac
    tagger = tag_fn or tag_aac
    last_error: Exception | None = None
    dest = dest_dir / output_filename(track)
    for candidate in candidates[:6]:
        video_id = str(candidate.get("videoId") or "")
        score = float(candidate.get("score") or confidence or 0)
        logger.info("Matched %s – %s → %s (%.2f)", track.get("artists"), track.get("title"), video_id, score)
        try:
            raw_path = downloader(video_id, staging_dir(), cookies_from_chrome=config.cookies_from_chrome)
            aac_path = converter(
                raw_path,
                dest,
                bitrate=config.aac_bitrate,
                sample_rate=config.sample_rate,
                channels=config.channels,
            )
            artwork = (cover_fn or cover_bytes)(track.get("cover_url"))
            tagger(aac_path, track, artwork)
            try:
                raw_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(RATE_LIMIT_S)
            return {
                "path": aac_path,
                "video_id": video_id,
                "confidence": score,
            }
        except Exception as exc:
            last_error = exc
            logger.info("Download failed for %s: %s", video_id, exc)
            continue
    raise DownloadError(str(last_error) if last_error else "All YouTube matches failed.")
