from __future__ import annotations

from pathlib import Path

from spotify_ipod.csvmusic_bridge import (
    download_tracks,
    load_overrides,
    parse_youtube_id,
    save_overrides,
    write_playlist_csv,
)


def test_write_playlist_csv(tmp_path: Path):
    path = tmp_path / "lib.csv"
    write_playlist_csv(
        [{"title": "Song", "artists": "Artist", "album": "Alb", "sp_id": "abc", "duration_ms": 1000}],
        "My Mix",
        path,
    )
    text = path.read_text()
    assert "Track name" in text
    assert "Song" in text
    assert "My Mix" in text


def test_download_tracks_maps_files(tmp_path: Path):
    track = {"title": "Song", "artists": "Artist", "sp_id": "abc", "album": "", "duration_ms": 0}

    def runner(csv_path, out_dir, playlist_name):
        assert csv_path.exists()
        folder = out_dir / "My Mix"
        folder.mkdir(parents=True)
        (folder / "Artist - Song.m4a").write_text("x")
        return 0

    result = download_tracks([track], tmp_path / "out", "My Mix", runner=runner)
    assert "abc" in result.files
    assert result.files["abc"].name == "Artist - Song.m4a"
    assert result.failed == []


def test_download_tracks_records_failures(tmp_path: Path):
    track = {"title": "Song", "artists": "Artist", "sp_id": "abc", "album": "", "duration_ms": 0}

    def runner(csv_path, out_dir, playlist_name):
        return 0

    result = download_tracks([track], tmp_path / "out", "My Mix", runner=runner)
    assert result.files == {}
    assert result.failed[0]["sp_id"] == "abc"


def test_parse_youtube_id():
    assert parse_youtube_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_load_overrides(tmp_path: Path):
    path = tmp_path / "overrides.json"
    path.write_text('{"abc123": "https://youtu.be/dQw4w9WgXcQ", "bad": ""}')
    assert load_overrides(path) == {"abc123": "dQw4w9WgXcQ"}


def test_save_overrides(tmp_path: Path):
    path = tmp_path / "overrides.json"
    save_overrides({"abc": "https://youtu.be/dQw4w9WgXcQ"}, path)
    assert load_overrides(path) == {"abc": "dQw4w9WgXcQ"}
    # merge + normalize
    save_overrides({"xyz": "dQw4w9WgXcQ"}, path)
    assert load_overrides(path) == {"abc": "dQw4w9WgXcQ", "xyz": "dQw4w9WgXcQ"}
