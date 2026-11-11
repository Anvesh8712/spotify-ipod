from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


@pytest.mark.skipif(sys.platform != "darwin", reason="Music.app exists on macOS")
def test_music_app_responds():
    from spotify_ipod.music import MusicLibrary

    name = MusicLibrary().ping()
    assert name == "Music"
