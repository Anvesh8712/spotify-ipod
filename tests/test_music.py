from __future__ import annotations

from pathlib import Path

from spotify_ipod.music import MusicLibrary, is_app_managed_path, playlist_marker
from spotify_ipod.paths import applescript_path


def test_playlist_marker_is_stable():
    assert playlist_marker("abc123") == "spotify-ipod:abc123"


def test_applescript_bridge_is_packaged():
    assert applescript_path().exists()
    text = applescript_path().read_text()
    assert "rebuildPlaylist" in text
    assert "trackLocation" in text
    assert "findAllByComment" in text
    assert "convertToAac" in text
    assert "setTrackComment" in text


def test_music_library_imports_once_and_drops_staging(tmp_path, monkeypatch):
    calls = []
    song = tmp_path / "home" / "staging" / "aac" / "song.m4a"
    song.parent.mkdir(parents=True)
    song.write_text("aac")
    media = tmp_path / "Music" / "Media" / "song.m4a"
    media.parent.mkdir(parents=True)
    media.write_text("aac")
    monkeypatch.setenv("SPOTIFY_IPOD_HOME", str(tmp_path / "home"))

    def runner(command, *args, script_path=None, **_kwargs):
        calls.append((command, args))
        if command == "find-comments":
            return ""
        if command == "find-comment":
            return ""
        if command == "import":
            return "PID9"
        if command == "track-location":
            return str(media)
        if command == "track-exists":
            return "yes"
        if command == "ensure-playlist":
            return "PL1"
        return "ok"

    library = MusicLibrary(runner=runner)
    result = library.import_file(song, "spotify-ipod:abc")
    assert result.persistent_id == "PID9"
    assert result.file_path == str(media)
    assert not song.exists()
    assert [c[0] for c in calls if c[0] == "import"] == ["import"]
    assert "find-comments" in [c[0] for c in calls]


def test_import_reuses_existing_and_deletes_duplicates(tmp_path, monkeypatch):
    calls = []
    song = tmp_path / "home" / "staging" / "aac" / "song.m4a"
    song.parent.mkdir(parents=True)
    song.write_text("aac")
    monkeypatch.setenv("SPOTIFY_IPOD_HOME", str(tmp_path / "home"))

    def runner(command, *args, script_path=None, **_kwargs):
        calls.append((command, args))
        if command == "find-comments":
            return "KEEP,DUP"
        if command == "track-location":
            return "/Music/Media/song.m4a"
        if command == "delete-track":
            return "ok"
        return "ok"

    library = MusicLibrary(runner=runner)
    result = library.import_file(song, "spotify-ipod:abc")
    assert result.persistent_id == "KEEP"
    assert ("delete-track", ("DUP",)) in calls
    assert "import" not in [c[0] for c in calls]
    assert not song.exists()


def test_is_app_managed_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_IPOD_HOME", str(tmp_path / "home"))
    managed = tmp_path / "home" / "staging" / "aac" / "song.m4a"
    managed.parent.mkdir(parents=True)
    managed.write_text("x")
    assert is_app_managed_path(managed) is True
    other = tmp_path / "elsewhere" / "song.m4a"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    assert is_app_managed_path(other) is False
