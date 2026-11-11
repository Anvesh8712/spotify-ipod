from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spotify_ipod.audio import aac_command, ffmpeg_bin, transcode_to_aac
from spotify_ipod.cli import main
from spotify_ipod.lock import SyncLock, SyncLockError
from spotify_ipod.scheduler import launch_agent_plist


def test_lock_prevents_overlap(tmp_path: Path):
    path = tmp_path / "sync.lock"
    first = SyncLock(path)
    first.acquire()
    second = SyncLock(path)
    with pytest.raises(SyncLockError):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_launch_agent_interval_follows_config():
    from spotify_ipod.config import AppConfig

    payload = launch_agent_plist(AppConfig(schedule_hours=3), executable=["/tmp/spotify-ipod", "sync"])
    assert payload["StartInterval"] == 10800
    assert payload["ProgramArguments"][-1] == "sync"


def test_status_command(app_home, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Client ID" in out
    assert "Playlists" in out


def test_ffmpeg_smoke_transcode(tmp_path: Path):
    try:
        ffmpeg = ffmpeg_bin()
    except Exception:
        pytest.skip("ffmpeg is required for AAC smoke conversion")
    src = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
        check=True,
        capture_output=True,
    )
    dst = tmp_path / "tone.m4a"
    transcode_to_aac(src, dst, bitrate="256k", sample_rate=44100, channels=2)
    assert dst.exists() and dst.stat().st_size > 0
    assert "-c:a" in aac_command(src, dst)
    probe = shutil.which("ffprobe") or str(Path(ffmpeg).with_name("ffprobe"))
    if Path(probe).exists():
        info = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "default=noprint_wrappers=1", str(dst)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "codec_name=aac" in info
        assert "sample_rate=44100" in info
        assert "channels=2" in info


@pytest.mark.skipif(sys.platform != "darwin", reason="Music.app exists on macOS")
def test_music_app_responds():
    from spotify_ipod.music import MusicLibrary

    name = MusicLibrary().ping()
    assert name == "Music"
