from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from spotify_ipod.chrome import BrowserError, open_in_chrome
from spotify_ipod.spotify import (
    SpotifyClient,
    SpotifyInvalidGrant,
    SpotifyTokens,
    _track_from_item,
    _user_playlist,
    authorization_url,
    create_pkce_pair,
    fetch_embed_playlist,
    is_made_for_you_playlist,
    merge_saved_playlists,
    parse_playlist_id,
    refresh_access_token,
    tokens_from_response,
)


def test_pkce_pair_is_url_safe():
    verifier, challenge = create_pkce_pair()
    assert len(verifier) > 40
    assert "=" not in challenge


FULL_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"


def test_authorization_url_includes_liked_songs_scope():
    url = authorization_url("cid", "http://127.0.0.1:8765/callback", "chal", "st", show_dialog=True)
    assert "user-library-read" in url
    url = authorization_url("cid", "http://127.0.0.1:8765/callback", "chal", "st", show_dialog=False)
    assert "show_dialog=false" in url
    assert "client_id=cid" in url
    first = authorization_url("cid", "http://127.0.0.1:8765/callback", "chal", "st", show_dialog=True)
    assert "show_dialog=true" in first


def test_refresh_token_is_rotated_when_spotify_returns_a_new_one():
    tokens = tokens_from_response(
        {
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        },
        "cid",
        previous_refresh="old-refresh",
    )
    assert tokens.refresh_token == "rotated-refresh"
    assert tokens.access_token == "new-access"


def test_refresh_token_kept_when_spotify_omits_it():
    tokens = tokens_from_response(
        {"access_token": "new-access", "expires_in": 60},
        "cid",
        previous_refresh="old-refresh",
    )
    assert tokens.refresh_token == "old-refresh"


def test_refresh_invalid_grant(monkeypatch):
    class FakeResponse:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant", "error_description": "Refresh token expired"}

    session = Mock()
    session.post.return_value = FakeResponse()
    with pytest.raises(SpotifyInvalidGrant):
        refresh_access_token("cid", "dead", session=session)


def test_client_refreshes_without_chrome():
    store = {}

    def load():
        return {
            "access_token": "old",
            "refresh_token": "refresh-me",
            "expires_at": time.time() - 10,
            "client_id": "cid",
            "scope": FULL_SCOPES,
        }

    def save(payload):
        store.update(payload)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"access_token": "fresh", "refresh_token": "next-refresh", "expires_in": 3600}

    session = Mock()
    session.post.return_value = FakeResponse()
    client = SpotifyClient("cid", store=save, load=load, session=session, authorize=lambda *a, **k: None)
    token = client.get_access_token(interactive=False)
    assert token == "fresh"
    assert store["refresh_token"] == "next-refresh"
    assert "user-library-read" in store["scope"]


def test_expired_refresh_opens_chrome_reauthorization():
    calls = {}

    def load():
        return {
            "access_token": "old",
            "refresh_token": "dead",
            "expires_at": time.time() - 10,
            "client_id": "cid",
            "scope": FULL_SCOPES,
        }

    saved = {}

    def save(payload):
        saved.update(payload)

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant"}

    def authorize(client_id, redirect_uri, *, show_dialog, session=None):
        calls["show_dialog"] = show_dialog
        calls["client_id"] = client_id
        return SpotifyTokens("reauthed", "new-refresh", time.time() + 3600, client_id)

    session = Mock()
    session.post.return_value = FakeResponse()
    client = SpotifyClient("cid", store=save, load=load, session=session, authorize=authorize)
    token = client.get_access_token(interactive=True)
    assert token == "reauthed"
    assert calls["show_dialog"] is False
    assert saved["refresh_token"] == "new-refresh"


def test_parse_playlist_id():
    assert parse_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_id("https://open.spotify.com/playlist/37i9dQZF1EVHGWrwldPRtj?si=27a10c4200de4dd7") == "37i9dQZF1EVHGWrwldPRtj"
    assert parse_playlist_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_id("liked-songs") == "liked-songs"
    assert parse_playlist_id("https://open.spotify.com/collection/tracks") == "liked-songs"


def test_missing_library_scope_prompts_chrome_login():
    calls = {}

    def load():
        return {
            "access_token": "old",
            "refresh_token": "refresh-me",
            "expires_at": time.time() + 3600,
            "client_id": "cid",
            "scope": "playlist-read-private playlist-read-collaborative",
        }

    saved = {}

    def save(payload):
        saved.update(payload)

    def authorize(client_id, redirect_uri, *, show_dialog, session=None):
        calls["show_dialog"] = show_dialog
        return SpotifyTokens("new", "new-refresh", time.time() + 3600, client_id, scope=FULL_SCOPES)

    client = SpotifyClient("cid", store=save, load=load, session=Mock(), authorize=authorize)
    token = client.get_access_token(interactive=True)
    assert token == "new"
    assert calls["show_dialog"] is True


def test_fetch_liked_songs_uses_library_endpoint():
    token = {
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": time.time() + 3600,
        "client_id": "cid",
        "scope": FULL_SCOPES,
    }

    class FakeResponse:
        def __init__(self, payload, status=200):
            self.status_code = status
            self.payload = payload
            self.headers = {}

        def json(self):
            return self.payload

    def get(url, headers=None, params=None, timeout=20):
        if url.endswith("/me/tracks"):
            return FakeResponse(
                {
                    "total": 1,
                    "next": None,
                    "items": [
                        {
                            "track": {
                                "id": "abc",
                                "type": "track",
                                "name": "Song",
                                "artists": [{"name": "Artist"}],
                                "album": {"name": "Album", "images": []},
                                "duration_ms": 1000,
                                "external_ids": {},
                            }
                        }
                    ],
                }
            )
        raise AssertionError(url)

    session = Mock()
    session.get.side_effect = get
    client = SpotifyClient("cid", store=lambda p: None, load=lambda: token, session=session)
    data = client.fetch_playlist("liked-songs")
    assert data["name"] == "Liked Songs"
    assert data["snapshot"] == ["abc"]
    assert data["tracks"][0]["title"] == "Song"


def test_open_in_chrome_rejects_non_http():
    with pytest.raises(BrowserError):
        open_in_chrome("file:///etc/passwd")


def test_user_playlist_reads_items_total():
    playlist = _user_playlist(
        {
            "id": "abc",
            "name": "To the ipod they go",
            "owner": {"id": "me", "display_name": "Anvesh"},
            "items": {"href": "https://api.spotify.com/v1/playlists/abc/items", "total": 207},
        },
        current_user_id="me",
    )
    assert playlist is not None
    assert playlist["total"] == 207
    assert playlist["owned"] is True
    assert playlist["made_for_you"] is False


def test_track_from_item_reads_renamed_item_field():
    parsed = _track_from_item(
        {
            "added_at": "2024-01-01T00:00:00Z",
            "item": {
                "id": "song1",
                "type": "track",
                "name": "Glitter & Honey",
                "artists": [{"name": "Del Water Gap"}],
                "album": {"name": "Album", "images": []},
                "duration_ms": 1000,
            },
        },
        "Mix",
        1,
    )
    assert parsed is not None
    assert parsed["title"] == "Glitter & Honey"
    assert parsed["sp_id"] == "song1"


def test_legacy_track_field_still_parses():
    parsed = _track_from_item(
        {
            "track": {
                "id": "abc",
                "type": "track",
                "name": "Song",
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album", "images": []},
                "duration_ms": 1000,
            }
        },
        "Liked Songs",
        1,
    )
    assert parsed is not None
    assert parsed["title"] == "Song"


def test_made_for_you_detection():
    assert is_made_for_you_playlist("Chill Mix")
    assert is_made_for_you_playlist("Daily Mix 3")
    assert is_made_for_you_playlist("Discover Weekly", "spotify")
    assert not is_made_for_you_playlist("To the ipod they go")


def test_fetch_playlist_parses_item_objects():
    token = {
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": time.time() + 3600,
        "client_id": "cid",
        "scope": FULL_SCOPES,
    }

    class FakeResponse:
        def __init__(self, payload, status=200):
            self.status_code = status
            self.payload = payload
            self.headers = {}

        def json(self):
            return self.payload

    def get(url, headers=None, params=None, timeout=20):
        if url.endswith("/playlists/abc"):
            return FakeResponse({"id": "abc", "name": "Mine", "external_urls": {}, "items": {"total": 1}})
        if url.endswith("/playlists/abc/items"):
            return FakeResponse(
                {
                    "total": 1,
                    "next": None,
                    "items": [
                        {
                            "item": {
                                "id": "song1",
                                "type": "track",
                                "name": "Glitter & Honey",
                                "artists": [{"name": "Del Water Gap"}],
                                "album": {"name": "Album", "images": []},
                                "duration_ms": 1000,
                            }
                        }
                    ],
                }
            )
        raise AssertionError(url)

    session = Mock()
    session.get.side_effect = get
    client = SpotifyClient("cid", store=lambda p: None, load=lambda: token, session=session)
    data = client.fetch_playlist("abc")
    assert data["tracks"][0]["title"] == "Glitter & Honey"
    assert data["snapshot"] == ["song1"]


CHILL_EMBED_HTML = """<script id="__NEXT_DATA__">{"props":{"pageProps":{"state":{"data":{"entity":{"id":"37i9dQZF1EVHGWrwldPRtj","name":"Chill Mix","subtitle":"Spotify","trackList":[{"uri":"spotify:track:5Ohxk2dO5COHF1krpoPigN","title":"Sign of the Times","subtitle":"Harry Styles","duration":340706,"entityType":"track"}]}}}}}}</script>"""


def test_embed_playlist_reads_share_page():
    class FakeResponse:
        status_code = 200
        text = CHILL_EMBED_HTML

    session = Mock()
    session.get.return_value = FakeResponse()
    data = fetch_embed_playlist("37i9dQZF1EVHGWrwldPRtj", session=session)
    assert data["name"] == "Chill Mix"
    assert data["from_share_link"] is True
    assert data["tracks"][0]["title"] == "Sign of the Times"
    assert data["tracks"][0]["artists"] == "Harry Styles"
    assert data["tracks"][0]["sp_id"] == "5Ohxk2dO5COHF1krpoPigN"


def test_fetch_playlist_falls_back_to_share_page():
    token = {
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_at": time.time() + 3600,
        "client_id": "cid",
        "scope": FULL_SCOPES,
    }

    class FakeApi:
        def __init__(self, payload, status=200, text=""):
            self.status_code = status
            self.payload = payload
            self.headers = {}
            self.text = text

        def json(self):
            return self.payload

    def get(url, headers=None, params=None, timeout=20):
        if "embed/playlist/37i9dQZF1EVHGWrwldPRtj" in url:
            return FakeApi({}, 200, CHILL_EMBED_HTML)
        if "/playlists/37i9dQZF1EVHGWrwldPRtj" in url:
            return FakeApi({"error": {"message": "Resource not found"}}, 404)
        raise AssertionError(url)

    session = Mock()
    session.get.side_effect = get
    client = SpotifyClient("cid", store=lambda p: None, load=lambda: token, session=session)
    data = client.fetch_playlist("37i9dQZF1EVHGWrwldPRtj")
    assert data["name"] == "Chill Mix"
    assert data["tracks"][0]["title"] == "Sign of the Times"


def test_merge_keeps_url_added_playlists():
    from spotify_ipod.config import PlaylistConfig

    remote = [{"id": "liked-songs", "name": "Liked Songs"}]
    saved = [
        PlaylistConfig(id="liked-songs", name="Liked Songs", url="", enabled=True),
        PlaylistConfig(
            id="37i9dQZF1EVHGWrwldPRtj",
            name="Chill Mix",
            url="https://open.spotify.com/playlist/37i9dQZF1EVHGWrwldPRtj",
            enabled=True,
            pinned=True,
        ),
    ]
    merged = merge_saved_playlists(remote, saved)
    assert [item["id"] for item in merged] == ["liked-songs", "37i9dQZF1EVHGWrwldPRtj"]
    assert merged[1]["pinned"] is True


def test_playlist_search_uses_cached_user_token_and_skips_unavailable_results():
    session = Mock()
    session.get.return_value.json.return_value = {
        "playlists": {
            "items": [None, {"id": "playlist", "name": "Chill Mix", "owner": {"display_name": "Spotify"}}],
            "next": "https://api.spotify.com/v1/search?offset=20",
        },
    }
    session.get.return_value.status_code = 200
    client = SpotifyClient("cid", session=session, load=lambda: {
        "client_id": "cid", "access_token": "cached", "refresh_token": "refresh",
        "expires_at": time.time() + 600,
        "scope": FULL_SCOPES,
    })
    playlists, has_next = client.search_playlists("  chill  ", page=1)

    assert [(item["name"], item["owner"]) for item in playlists] == [("Chill Mix", "Spotify")]
    assert has_next
    assert session.get.call_args.args[0].endswith("/search")
    assert session.get.call_args.kwargs["params"] == {
        "q": "chill", "type": "playlist", "limit": 10, "offset": 10,
    }
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer cached"


def test_playlist_search_includes_editorial_result_missing_from_api():
    client = SpotifyClient("cid")
    client._get = Mock(return_value={"playlists": {"items": [
        {"id": "fan-playlist", "name": "Bedroom Pop", "owner": {"display_name": "Fan"}},
    ]}})

    results, _ = client.search_playlists("bedroom pop")

    assert [(item["id"], item["owner"]) for item in results] == [
        ("37i9dQZF1DXcxvFzl58uP7", "Spotify"),
        ("fan-playlist", "Fan"),
    ]
    next_page, _ = client.search_playlists("bedroom pop", page=1)
    assert all(item["owner"] != "Spotify" for item in next_page)
