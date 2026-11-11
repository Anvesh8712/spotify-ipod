from __future__ import annotations

from pathlib import Path

from spotify_ipod.csvmusic_bridge import (
    _candidate_rows,
    download_tracks,
    load_overrides,
    load_unmatched,
    parse_youtube_id,
    save_overrides,
    save_unmatched,
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


def test_candidate_rows_normalize_options():
    rows = _candidate_rows(
        [
            {
                "videoId": "dQw4w9WgXcQ",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "score": 0.55,
            }
        ]
    )
    assert rows == [
        {"video_id": "dQw4w9WgXcQ", "title": "Song", "artists": "Artist", "score": 0.55}
    ]


def test_unmatched_roundtrip_keeps_candidates(tmp_path: Path):
    path = tmp_path / "unmatched.json"
    items = [
        {
            "sp_id": "abc",
            "title": "Song",
            "artists": "Artist",
            "reason": "low confidence (0.55)",
            "confidence": 0.55,
            "candidates": [
                {"video_id": "dQw4w9WgXcQ", "title": "Song", "artists": "Artist", "score": 0.55}
            ],
        }
    ]
    save_unmatched(items, path)
    loaded = load_unmatched(path)
    assert loaded[0]["sp_id"] == "abc"
    assert loaded[0]["confidence"] == 0.55
    assert loaded[0]["candidates"][0]["video_id"] == "dQw4w9WgXcQ"


def test_download_tracks_overlaps_downloads_and_only_publishes_successes(tmp_path, monkeypatch):
    from threading import Barrier

    import pytest

    downloader = pytest.importorskip("csvmusic.core.downloader")
    from csvmusic.core import paths

    together = Barrier(3)
    titles = ("One", "Two", "Three")
    tracks = [
        {"title": title, "artists": "Artist", "sp_id": title}
        for title in titles
    ]

    def fake_download(video_id, folder, base, *, yt_dlp_bin):
        together.wait(timeout=5)
        path = folder / f"{base}.m4a"
        path.write_text("audio")
        if video_id == "Three":
            raise OSError("download interrupted")
        return path

    monkeypatch.setattr(paths, "ytdlp_path", lambda: paths.INTERNAL_YTDLP)
    monkeypatch.setattr(downloader, "download_m4a", fake_download)
    monkeypatch.setattr(downloader, "yt_thumbnail_bytes", lambda _: None)
    monkeypatch.setattr(downloader, "tag_file", lambda *_: None)
    result = download_tracks(tracks, tmp_path, "Mix", overrides={title: title for title in titles})

    assert set(result.files) == {"One", "Two"}
    assert [item["sp_id"] for item in result.failed] == ["Three"]
    assert sorted(path.name for path in (tmp_path / "Mix").iterdir()) == [
        "Artist - One.m4a", "Artist - Two.m4a",
    ]
