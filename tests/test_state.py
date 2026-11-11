from __future__ import annotations

import json
from pathlib import Path

from spotify_ipod.state import (
    AppState,
    TrackRecord,
    load_state,
    orphan_track_ids,
    playlist_changed,
    save_state,
    track_key,
)


def test_track_key_prefers_spotify_id():
    assert track_key({"sp_id": "abc", "title": "X"}) == "abc"


def test_track_key_local_fallback():
    key = track_key({"title": "Hello", "artists": "World", "duration_ms": 1234})
    assert key.startswith("local:")
    assert "hello" in key


def test_playlist_order_and_identity_changes():
    assert playlist_changed(["a", "b"], ["a", "b"]) is False
    assert playlist_changed(["a", "b"], ["b", "a"]) is True
    assert playlist_changed(["a", "b"], ["a", "b", "c"]) is True
    assert playlist_changed(["a", "a"], ["a"]) is True


def test_shared_track_orphan_cleanup():
    state = AppState(
        tracks={
            "keep": TrackRecord(sp_id="keep", title="Keep", artists="A"),
            "gone": TrackRecord(sp_id="gone", title="Gone", artists="B"),
            "shared": TrackRecord(sp_id="shared", title="Shared", artists="C"),
        }
    )
    current = {
        "playlist-a": ["keep", "shared"],
        "playlist-b": ["shared"],
    }
    assert orphan_track_ids(state, current) == ["gone"]


def test_duplicate_entries_are_preserved_in_snapshot():
    snapshot = ["a", "b", "a"]
    assert playlist_changed(["a", "b", "a"], snapshot) is False
    assert playlist_changed(["a", "a", "b"], snapshot) is True


def test_atomic_state_replace(tmp_path: Path):
    path = tmp_path / "state.json"
    state = AppState(tracks={"x": TrackRecord(sp_id="x", title="X", artists="Y")})
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.track("x").title == "X"
    assert not path.with_name("state.json.tmp").exists()

    save_state(AppState(tracks={"z": TrackRecord(sp_id="z", title="Z", artists="Q")}), path)
    payload = json.loads(path.read_text())
    assert "z" in payload["tracks"]
    assert "x" not in payload["tracks"]
