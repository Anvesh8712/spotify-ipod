from __future__ import annotations

from pathlib import Path

from spotify_ipod.config import AppConfig, PlaylistConfig
from spotify_ipod.csvmusic_bridge import DownloadError
from spotify_ipod.pipeline import SyncPipeline
from spotify_ipod.state import AppState, save_state


class FakeSpotify:
    def __init__(self, playlists: dict[str, dict]) -> None:
        self.playlists = playlists

    def fetch_playlist(self, playlist_id: str) -> dict:
        return self.playlists[playlist_id]


class FakeMusic:
    def __init__(self) -> None:
        self.files: list[str] = []
        self.playlists: dict[str, list[str]] = {}
        self.deleted: list[str] = []
        self.comments: dict[str, str] = {}
        self.ids = 0
        self.existing: set[str] = set()
        self.locations: dict[str, str] = {}
        self.converted_from: list[str] = []

    def import_file(self, path: Path, marker: str = ""):
        from spotify_ipod.music import ImportResult

        self.ids += 1
        pid = f"PID{self.ids}"
        self.files.append(str(path))
        self.existing.add(pid)
        media = path.parent / "MusicMedia" / path.name
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(path.read_bytes() if path.exists() else b"aac")
        if path.exists():
            path.unlink()
        self.locations[pid] = str(media)
        if marker:
            self.comments[pid] = marker
        return ImportResult(persistent_id=pid, file_path=str(media))

    def set_comment(self, persistent_id: str, marker: str) -> None:
        self.comments[persistent_id] = marker

    def convert_to_aac(self, persistent_id: str):
        from spotify_ipod.music import ImportResult

        self.converted_from.append(persistent_id)
        old_loc = self.locations.get(persistent_id, "")
        self.ids += 1
        new_pid = f"PID{self.ids}"
        self.existing.discard(persistent_id)
        self.existing.add(new_pid)
        self.deleted.append(persistent_id)
        self.locations[new_pid] = old_loc
        if persistent_id in self.comments:
            self.comments[new_pid] = self.comments.pop(persistent_id)
        return ImportResult(persistent_id=new_pid, file_path=old_loc)

    def track_location(self, persistent_id: str) -> str:
        return self.locations.get(persistent_id, "")

    def track_exists(self, persistent_id: str) -> bool:
        return persistent_id in self.existing

    def ensure_playlist(self, name: str, spotify_playlist_id: str) -> str:
        key = f"PL-{spotify_playlist_id}"
        self.playlists.setdefault(key, [])
        return key

    def rebuild_playlist(self, playlist_persistent_id: str, track_persistent_ids: list[str]) -> None:
        self.playlists[playlist_persistent_id] = list(track_persistent_ids)

    def delete_track(self, persistent_id: str) -> None:
        self.deleted.append(persistent_id)
        self.existing.discard(persistent_id)


def _track(sp_id: str, title: str) -> dict:
    return {
        "sp_id": sp_id,
        "title": title,
        "artists": "Artist",
        "album": "Album",
        "duration_ms": 180000,
        "cover_url": None,
    }


def _playlist(pid: str, name: str, tracks: list[dict]) -> dict:
    return {
        "id": pid,
        "name": name,
        "url": f"https://open.spotify.com/playlist/{pid}",
        "tracks": tracks,
        "snapshot": [t["sp_id"] for t in tracks],
    }


def _pipeline(tmp_path: Path, fake_spotify: FakeSpotify, music: FakeMusic, playlists: list[PlaylistConfig], state: AppState | None = None) -> SyncPipeline:
    config = AppConfig(
        spotify_client_id="cid",
        playlists=playlists,
        output_dir=str(tmp_path / "library"),
    )

    def prepare(track, dest_dir, config):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{track['sp_id']}.m4a"
        path.write_text("aac")
        return {"path": path, "video_id": "vid", "confidence": 0.9}

    def persist(st):
        save_state(st, tmp_path / "state.json")

    return SyncPipeline(
        config,
        spotify=fake_spotify,
        music=music,
        state=state or AppState(),
        prepare=prepare,
        persist=persist,
    )


def test_add_remove_reorder_and_no_change(tmp_path: Path):
    a = _track("a", "One")
    b = _track("b", "Two")
    c = _track("c", "Three")
    music = FakeMusic()
    spotify = FakeSpotify({"p1": _playlist("p1", "Mix", [a, b])})
    pipe = _pipeline(tmp_path, spotify, music, [PlaylistConfig(id="p1", name="Mix", url="u")])
    report = pipe.run()
    assert report.rebuilt == ["Mix"]
    # import a→PID1 convert→PID2; import b→PID3 convert→PID4
    assert music.playlists["PL-p1"] == ["PID2", "PID4"]
    assert set(report.downloaded) == {"a.m4a", "b.m4a"}
    assert music.converted_from == ["PID1", "PID3"]

    spotify.playlists["p1"] = _playlist("p1", "Mix", [b, a])
    report = pipe.run()
    assert music.playlists["PL-p1"] == ["PID4", "PID2"]
    assert report.downloaded == []
    assert report.rebuilt == ["Mix"]

    report = pipe.run()
    assert report.unchanged == ["Mix"]
    assert report.rebuilt == []

    spotify.playlists["p1"] = _playlist("p1", "Mix", [b, c])
    report = pipe.run()
    assert "c.m4a" in report.downloaded
    # c: import PID5 → convert PID6
    assert music.playlists["PL-p1"] == ["PID4", "PID6"]
    assert report.orphans_deleted == []
    # originals deleted by convert, not orphan cleanup
    assert pipe.state.track("a") is not None


def test_removed_tracks_stay_in_music_library(tmp_path: Path):
    shared = _track("s", "Shared")
    only_a = _track("a", "OnlyA")
    music = FakeMusic()
    spotify = FakeSpotify(
        {
            "p1": _playlist("p1", "One", [shared, only_a]),
            "p2": _playlist("p2", "Two", [shared]),
        }
    )
    playlists = [
        PlaylistConfig(id="p1", name="One", url="u"),
        PlaylistConfig(id="p2", name="Two", url="u"),
    ]
    pipe = _pipeline(tmp_path, spotify, music, playlists)
    pipe.run()
    shared_pid = pipe.state.track("s").music_persistent_id
    only_a_pid = pipe.state.track("a").music_persistent_id
    spotify.playlists["p1"] = _playlist("p1", "One", [only_a])
    report = pipe.run()
    assert shared_pid not in music.deleted
    assert report.orphans_deleted == []
    spotify.playlists["p2"] = _playlist("p2", "Two", [])
    report = pipe.run()
    assert report.orphans_deleted == []
    assert shared_pid not in music.deleted
    assert only_a_pid not in music.deleted
    assert pipe.state.track("s") is not None


def test_dry_run_does_not_import(tmp_path: Path):
    track = _track("a", "One")
    music = FakeMusic()
    spotify = FakeSpotify({"p1": _playlist("p1", "Mix", [track])})
    pipe = _pipeline(tmp_path, spotify, music, [PlaylistConfig(id="p1", name="Mix", url="u")])
    report = pipe.run(dry_run=True)
    assert report.dry_run is True
    assert music.files == []
    assert pipe.state.tracks == {}


def test_duplicate_playlist_entries_keep_order(tmp_path: Path):
    a = _track("a", "One")
    music = FakeMusic()
    spotify = FakeSpotify({"p1": _playlist("p1", "Mix", [a, a])})
    pipe = _pipeline(tmp_path, spotify, music, [PlaylistConfig(id="p1", name="Mix", url="u")])
    pipe.run()
    assert music.playlists["PL-p1"] == ["PID2", "PID2"]


def test_crash_safe_persist_between_tracks(tmp_path: Path):
    a = _track("a", "One")
    b = _track("b", "Two")
    music = FakeMusic()

    def prepare(track, dest_dir, config):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{track['sp_id']}.m4a"
        path.write_text("aac")
        return {"path": path, "video_id": "vid", "confidence": 0.9}

    def persist(state):
        save_state(state, tmp_path / "state.json")
        if "a" in state.tracks and "b" not in state.tracks:
            raise RuntimeError("simulated crash")

    config = AppConfig(
        spotify_client_id="cid",
        playlists=[PlaylistConfig(id="p1", name="Mix", url="u")],
        output_dir=str(tmp_path / "library"),
    )
    pipe = SyncPipeline(
        config,
        spotify=FakeSpotify({"p1": _playlist("p1", "Mix", [a, b])}),
        music=music,
        state=AppState(),
        prepare=prepare,
        persist=persist,
    )
    try:
        pipe.run()
    except RuntimeError:
        pass
    saved = Path(tmp_path / "state.json")
    assert saved.exists()
    text = saved.read_text()
    assert '"a"' in text
    assert '"b"' not in text


def test_partial_failures_still_rebuild_playlist(tmp_path: Path):
    a = _track("a", "One")
    b = _track("b", "Two")
    c = _track("c", "Three")
    music = FakeMusic()
    spotify = FakeSpotify({"p1": _playlist("p1", "Mix", [a, b, c])})

    def prepare(track, dest_dir, config):
        if track["sp_id"] == "b":
            raise DownloadError("age-restricted")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{track['sp_id']}.m4a"
        path.write_text("aac")
        return {"path": path, "video_id": "vid", "confidence": 0.9}

    config = AppConfig(
        spotify_client_id="cid",
        playlists=[PlaylistConfig(id="p1", name="Mix", url="u")],
        output_dir=str(tmp_path / "library"),
    )
    pipe = SyncPipeline(
        config,
        spotify=spotify,
        music=music,
        state=AppState(),
        prepare=prepare,
        persist=lambda st: save_state(st, tmp_path / "state.json"),
    )
    report = pipe.run()
    assert report.rebuilt == ["Mix"]
    # a→PID2, c→PID4 (b failed before import)
    assert music.playlists["PL-p1"] == ["PID2", "PID4"]
    assert any("Two" in error for error in report.errors)
    assert pipe.state.track("b") is None


def test_csvmusic_batch_path(tmp_path: Path):
    a = _track("a", "One")
    music = FakeMusic()
    spotify = FakeSpotify({"p1": _playlist("p1", "Mix", [a])})
    calls = []

    def download_missing(tracks, dest_dir, playlist_name):
        calls.append((len(tracks), playlist_name))
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "Artist - One.m4a"
        path.write_text("from-csvmusic")
        return {"a": path}

    config = AppConfig(
        spotify_client_id="cid",
        playlists=[PlaylistConfig(id="p1", name="Mix", url="u")],
        output_dir=str(tmp_path / "library"),
    )
    pipe = SyncPipeline(
        config,
        spotify=spotify,
        music=music,
        state=AppState(),
        download_missing=download_missing,
        persist=lambda st: save_state(st, tmp_path / "state.json"),
    )
    report = pipe.run()
    assert calls == [(1, "Mix")]
    assert "Artist - One.m4a" in report.downloaded
    assert music.playlists["PL-p1"] == ["PID2"]
    assert music.converted_from == ["PID1"]
