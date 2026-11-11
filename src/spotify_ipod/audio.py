from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover

from spotify_ipod.config import DEFAULT_AAC_BITRATE, DEFAULT_CHANNELS, DEFAULT_SAMPLE_RATE


class AudioError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    override = os.environ.get("FFMPEG_BIN")
    if override:
        path = Path(override)
        if path.exists():
            return str(path)
        which_override = shutil.which(override)
        if which_override:
            return which_override
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in (
        Path("/opt/homebrew/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
    ):
        if candidate.exists():
            return str(candidate)
    raise AudioError("ffmpeg is not installed. Install it with `brew install ffmpeg`.")


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def aac_command(
    src: Path,
    dst: Path,
    *,
    bitrate: str = DEFAULT_AAC_BITRATE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    ffmpeg: str = "ffmpeg",
    encoder: str = "aac_at",
) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-vn",
        "-sn",
        "-c:a",
        encoder,
        "-b:a",
        bitrate,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-movflags",
        "+faststart",
        str(dst),
    ]


def _choose_aac_encoder(ffmpeg: str) -> str:
    """Prefer Apple's AudioToolbox AAC — older iPods often glitch on ffmpeg's native AAC."""
    result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True)
    blob = (result.stdout or "") + (result.stderr or "")
    if re.search(r"\baac_at\b", blob):
        return "aac_at"
    return "aac"


def _bitrate_ok(raw: str | None, target: str) -> bool:
    if not raw or not raw.isdigit():
        return False
    try:
        want = int(str(target).rstrip("kK")) * 1000
    except ValueError:
        return True
    actual = int(raw)
    return abs(actual - want) <= max(40_000, want // 5)


def assert_ipod_aac(
    path: Path,
    *,
    bitrate: str = DEFAULT_AAC_BITRATE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> dict[str, str]:
    info = probe_audio(path)
    codec = (info.get("codec_name") or "").lower()
    if codec != "aac":
        raise AudioError(f"{path.name} is {codec or 'unknown'}, expected AAC.")
    if info.get("sample_rate") != str(sample_rate):
        raise AudioError(f"{path.name} sample rate is {info.get('sample_rate')}, expected {sample_rate}.")
    if info.get("channels") != str(channels):
        raise AudioError(f"{path.name} channels is {info.get('channels')}, expected {channels}.")
    if not _bitrate_ok(info.get("bit_rate"), bitrate):
        raise AudioError(f"{path.name} bitrate is {info.get('bit_rate')}, expected ~{bitrate}.")
    return info


def transcode_to_aac(
    src: Path,
    dst: Path,
    *,
    bitrate: str = DEFAULT_AAC_BITRATE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp.m4a")
    if tmp.exists():
        tmp.unlink()
    ffmpeg = ffmpeg_bin()
    encoder = _choose_aac_encoder(ffmpeg)
    cmd = aac_command(
        src,
        tmp,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
        encoder=encoder,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip().splitlines()
        raise AudioError(detail[-1] if detail else "ffmpeg AAC conversion failed.")
    try:
        assert_ipod_aac(tmp, bitrate=bitrate, sample_rate=sample_rate, channels=channels)
    except AudioError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)
    return dst


def probe_audio(path: Path) -> dict[str, str]:
    probe = ffprobe_bin()
    if not probe:
        return {}
    result = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,bit_rate",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key.strip()] = value.strip()
    return info


def sanitize_name(name: str) -> str:
    text = unicodedata.normalize("NFC", name or "")
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    text = re.sub(r"[\x00-\x1f]+", "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    text = text or "_"
    if len(text) <= 120:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{text[:109].rstrip()}-{digest}"


def tag_aac(path: Path, meta: dict, cover_bytes: bytes | None = None) -> None:
    audio = MP4(path)
    audio["\xa9nam"] = [str(meta.get("title") or "")]
    audio["\xa9ART"] = [str(meta.get("artists") or "")]
    audio["\xa9alb"] = [str(meta.get("album") or "")]
    if meta.get("year"):
        audio["\xa9day"] = [str(meta["year"])]
    if meta.get("track_no"):
        audio["trkn"] = [(int(meta["track_no"]), 0)]
    if meta.get("disc_no"):
        audio["disk"] = [(int(meta["disc_no"]), 0)]
    audio["\xa9cmt"] = [f"spotify-ipod:{meta.get('sp_id') or ''}"]
    if cover_bytes:
        fmt = MP4Cover.FORMAT_JPEG
        if cover_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            fmt = MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
    audio.save()
