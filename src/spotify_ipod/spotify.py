from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from spotify_ipod.chrome import open_in_chrome
from spotify_ipod.config import DEFAULT_REDIRECT_URI
from spotify_ipod.keychain import load_secret, store_secret

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE_URL = "https://api.spotify.com/v1"
DEFAULT_SCOPES = ("playlist-read-private", "playlist-read-collaborative", "user-library-read")
LIKED_SONGS_ID = "liked-songs"
LIKED_SONGS_URL = "https://open.spotify.com/collection/tracks"
ACCESS_TOKEN_SKEW_S = 60
MADE_FOR_YOU_OWNER_ID = "spotify"
MADE_FOR_YOU_CATEGORY_IDS = ("0JQ5DAt0tbjZptfcdMSKl3", "made-for-you")
_MADE_FOR_YOU_NAMES = {
    "discover weekly",
    "release radar",
    "chill mix",
    "on repeat",
    "repeat rewind",
    "your time capsule",
    "daily wellness",
    "daily drive",
}
_DAILY_MIX_NAME = re.compile(r"^daily mix \s*\d+$", re.I)
EMBED_PLAYLIST_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
EMBED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


def has_required_scopes(scope: str, required: tuple[str, ...] = DEFAULT_SCOPES) -> bool:
    granted = set((scope or "").split())
    return set(required) <= granted


class SpotifyError(RuntimeError):
    pass


class SpotifyAuthError(SpotifyError):
    pass


class SpotifyInvalidGrant(SpotifyAuthError):
    pass


@dataclass
class SpotifyTokens:
    access_token: str
    refresh_token: str
    expires_at: float
    client_id: str
    token_type: str = "Bearer"
    scope: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - ACCESS_TOKEN_SKEW_S)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "client_id": self.client_id,
            "token_type": self.token_type,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SpotifyTokens | None":
        if not data:
            return None
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or "")
        if not access or not refresh:
            return None
        return cls(
            access_token=access,
            refresh_token=refresh,
            expires_at=float(data.get("expires_at") or 0),
            client_id=str(data.get("client_id") or ""),
            token_type=str(data.get("token_type") or "Bearer"),
            scope=str(data.get("scope") or ""),
        )


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorization_url(
    client_id: str,
    redirect_uri: str,
    challenge: str,
    state: str,
    *,
    show_dialog: bool,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": " ".join(scopes),
        "show_dialog": "true" if show_dialog else "false",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def tokens_from_response(
    data: dict[str, Any],
    client_id: str,
    previous_refresh: str = "",
    previous_scope: str = "",
) -> SpotifyTokens:
    access = str(data.get("access_token") or "")
    if not access:
        raise SpotifyAuthError("Spotify did not return an access token.")
    refresh = str(data.get("refresh_token") or previous_refresh)
    if not refresh:
        raise SpotifyAuthError("Spotify did not return a refresh token.")
    expires_in = int(data.get("expires_in") or 3600)
    return SpotifyTokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
        client_id=client_id,
        token_type=str(data.get("token_type") or "Bearer"),
        scope=str(data.get("scope") or previous_scope),
    )


def _token_error(response: requests.Response) -> SpotifyAuthError:
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = str(data.get("error") or "")
    description = str(data.get("error_description") or f"HTTP {response.status_code}")
    if error == "invalid_grant" or response.status_code == 400 and "invalid_grant" in description:
        return SpotifyInvalidGrant(description)
    return SpotifyAuthError(description)


def exchange_authorization_code(
    client_id: str,
    code: str,
    redirect_uri: str,
    verifier: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 20,
) -> SpotifyTokens:
    client = session or requests.Session()
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SpotifyAuthError(f"Could not exchange Spotify authorization: {exc}") from exc
    if response.status_code >= 400:
        raise _token_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise SpotifyAuthError("Spotify returned invalid authorization data.") from exc
    if not isinstance(data, dict):
        raise SpotifyAuthError("Spotify returned invalid authorization data.")
    return tokens_from_response(data, client_id)


def refresh_access_token(
    client_id: str,
    refresh_token: str,
    *,
    previous_scope: str = "",
    session: requests.Session | None = None,
    timeout: int = 20,
) -> SpotifyTokens:
    client = session or requests.Session()
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise SpotifyAuthError(f"Could not refresh Spotify authorization: {exc}") from exc
    if response.status_code >= 400:
        raise _token_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise SpotifyAuthError("Spotify returned invalid refresh data.") from exc
    if not isinstance(data, dict):
        raise SpotifyAuthError("Spotify returned invalid refresh data.")
    return tokens_from_response(data, client_id, previous_refresh=refresh_token, previous_scope=previous_scope)


class _CallbackServer(HTTPServer):
    auth_code: str | None = None
    auth_error: str | None = None
    auth_state: str | None = None


def _callback_handler_factory(expected_path: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(parsed.query)
            server = self.server
            assert isinstance(server, _CallbackServer)
            server.auth_code = (query.get("code") or [None])[0]
            server.auth_error = (query.get("error") or [None])[0]
            server.auth_state = (query.get("state") or [None])[0]
            body = b"<html><body><p>Spotify authorization complete. You can close this tab.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    return Handler


def wait_for_callback(redirect_uri: str, expected_state: str, timeout: int = 180) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    server = _CallbackServer((host, port), _callback_handler_factory(path))
    server.timeout = 0.5
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not server.auth_code and not server.auth_error:
            server.handle_request()
    finally:
        server.server_close()
    if server.auth_error:
        raise SpotifyAuthError(f"Spotify authorization failed: {server.auth_error}")
    if not server.auth_code:
        raise SpotifyAuthError("Timed out waiting for Spotify to authorize in Chrome. Confirm the prompt if Chrome is asking.")
    if server.auth_state != expected_state:
        raise SpotifyAuthError("Spotify authorization state did not match.")
    return server.auth_code


def authorize_with_chrome(
    client_id: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    *,
    show_dialog: bool,
    open_browser: Callable[[str], None] = open_in_chrome,
    wait: Callable[[str, str, int], str] = wait_for_callback,
    session: requests.Session | None = None,
    timeout: int = 180,
) -> SpotifyTokens:
    if not client_id.strip():
        raise SpotifyAuthError("Add your Spotify app Client ID before signing in.")
    verifier, challenge = create_pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorization_url(client_id, redirect_uri, challenge, state, show_dialog=show_dialog)
    open_browser(url)
    code = wait(redirect_uri, state, timeout)
    return exchange_authorization_code(client_id, code, redirect_uri, verifier, session=session)


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        *,
        session: requests.Session | None = None,
        store: Callable[[dict[str, Any]], None] = store_secret,
        load: Callable[[], dict[str, Any] | None] = load_secret,
        authorize: Callable[..., SpotifyTokens] = authorize_with_chrome,
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.session = session or requests.Session()
        self._store = store
        self._load = load
        self._authorize = authorize

    def save_tokens(self, tokens: SpotifyTokens) -> SpotifyTokens:
        self._store(tokens.to_dict())
        return tokens

    def load_tokens(self) -> SpotifyTokens | None:
        return SpotifyTokens.from_dict(self._load())

    def get_access_token(self, *, interactive: bool = True) -> str:
        tokens = self.load_tokens()
        if tokens and tokens.client_id and tokens.client_id != self.client_id:
            tokens = None
        if tokens and not tokens.expired and has_required_scopes(tokens.scope):
            return tokens.access_token
        if tokens and tokens.refresh_token and has_required_scopes(tokens.scope):
            try:
                refreshed = refresh_access_token(
                    self.client_id,
                    tokens.refresh_token,
                    previous_scope=tokens.scope,
                    session=self.session,
                )
                return self.save_tokens(refreshed).access_token
            except SpotifyInvalidGrant:
                if not interactive:
                    raise SpotifyAuthError(
                        "Spotify refresh token expired. Open the app and sign in again, or wait for Chrome reauthorization."
                    )
                tokens = self._authorize(
                    self.client_id,
                    self.redirect_uri,
                    show_dialog=False,
                    session=self.session,
                )
                return self.save_tokens(tokens).access_token
        if not interactive:
            raise SpotifyAuthError("Spotify is not signed in, or extra permission is needed for Liked Songs.")
        tokens = self._authorize(
            self.client_id,
            self.redirect_uri,
            show_dialog=True,
            session=self.session,
        )
        return self.save_tokens(tokens).access_token

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.get_access_token(interactive=True)
        try:
            response = self.session.get(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc}") from exc
        if response.status_code == 401:
            raise SpotifyAuthError("Spotify rejected the access token. Sign in again.")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            suffix = f" Retry after {retry_after} seconds." if retry_after else ""
            raise SpotifyError(f"Spotify rate limit reached.{suffix}")
        if response.status_code >= 400:
            raise SpotifyError(_spotify_http_error(response, path))
        data = response.json()
        if not isinstance(data, dict):
            raise SpotifyError("Spotify returned an unexpected response.")
        return data

    def current_user(self) -> dict[str, Any]:
        return self._get("/me")

    def list_playlists(self) -> list[dict[str, Any]]:
        me_id = ""
        try:
            me_id = str(self.current_user().get("id") or "")
        except SpotifyError:
            me_id = ""
        playlists: list[dict[str, Any]] = [self._liked_songs_summary()]
        seen = {LIKED_SONGS_ID}
        offset = 0
        while True:
            page = self._get("/me/playlists", params={"limit": 50, "offset": offset})
            items = page.get("items")
            if not isinstance(items, list):
                raise SpotifyError("Spotify returned playlists in an unexpected format.")
            for item in items:
                playlist = _user_playlist(item, current_user_id=me_id)
                if playlist and playlist["id"] not in seen:
                    seen.add(playlist["id"])
                    playlists.append(playlist)
            offset += len(items)
            if not items or not page.get("next"):
                break
        for extra in self._made_for_you_playlists(me_id):
            if extra["id"] not in seen:
                seen.add(extra["id"])
                playlists.append(extra)
        head, rest = playlists[:1], playlists[1:]
        rest.sort(key=_playlist_sort_key)
        return head + rest

    def fetch_playlist(self, playlist_id: str) -> dict[str, Any]:
        if is_liked_songs_id(playlist_id):
            return self._fetch_liked_songs()
        try:
            return self._fetch_api_playlist(playlist_id)
        except SpotifyError as api_error:
            try:
                public = fetch_embed_playlist(playlist_id, session=self.session)
            except SpotifyError:
                raise api_error from api_error
            return public

    def _fetch_api_playlist(self, playlist_id: str) -> dict[str, Any]:
        summary = self._get(f"/playlists/{playlist_id}", params={"fields": "id,name,external_urls,items.total"})
        name = str(summary.get("name") or "Spotify Playlist").strip()
        url = ((summary.get("external_urls") or {}) if isinstance(summary.get("external_urls"), dict) else {}).get("spotify")
        tracks = self._paged_tracks(
            f"/playlists/{playlist_id}/items",
            name,
            extra_params={"additional_types": "track", "market": "from_token"},
        )
        reported = _playlist_item_total(summary)
        if not tracks and reported:
            return fetch_embed_playlist(playlist_id, session=self.session)
        return {
            "id": playlist_id,
            "name": name,
            "url": url or f"https://open.spotify.com/playlist/{playlist_id}",
            "tracks": tracks,
            "snapshot": [str(track["sp_id"]) for track in tracks],
            "from_share_link": False,
        }

    def _made_for_you_playlists(self, current_user_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for category_id in MADE_FOR_YOU_CATEGORY_IDS:
            try:
                page = self._get(
                    f"/browse/categories/{category_id}/playlists",
                    params={"limit": 50},
                )
            except SpotifyError:
                continue
            payload = page.get("playlists") if isinstance(page.get("playlists"), dict) else page
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                playlist = _user_playlist(item, current_user_id=current_user_id)
                if playlist:
                    playlist["made_for_you"] = True
                    found.append(playlist)
        return found

    def _liked_songs_summary(self) -> dict[str, Any]:
        page = self._get("/me/tracks", params={"limit": 1, "offset": 0})
        return {
            "id": LIKED_SONGS_ID,
            "name": "Liked Songs",
            "owner": "You",
            "owner_id": "",
            "total": int(page.get("total") or 0),
            "collaborative": False,
            "public": False,
            "url": LIKED_SONGS_URL,
            "owned": True,
            "made_for_you": False,
        }

    def _fetch_liked_songs(self) -> dict[str, Any]:
        tracks = self._paged_tracks("/me/tracks", "Liked Songs")
        return {
            "id": LIKED_SONGS_ID,
            "name": "Liked Songs",
            "url": LIKED_SONGS_URL,
            "tracks": tracks,
            "snapshot": [str(track["sp_id"]) for track in tracks],
        }

    def _paged_tracks(self, path: str, playlist_name: str, extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {"limit": 50, "offset": offset}
            if extra_params:
                params.update(extra_params)
            page = self._get(path, params=params)
            items = page.get("items")
            if not isinstance(items, list):
                raise SpotifyError("Spotify returned playlist tracks in an unexpected format.")
            for item in items:
                track = _track_from_item(item, playlist_name, len(tracks) + 1)
                if track:
                    tracks.append(track)
            offset += len(items)
            if not items or not page.get("next"):
                break
        return tracks


def is_liked_songs_id(value: str) -> bool:
    text = (value or "").strip().casefold()
    return text in {LIKED_SONGS_ID, "liked", "liked songs", "collection/tracks"}


def is_made_for_you_playlist(name: str, owner_id: str = "") -> bool:
    if (owner_id or "").strip().casefold() == MADE_FOR_YOU_OWNER_ID:
        return True
    text = (name or "").strip().casefold()
    return text in _MADE_FOR_YOU_NAMES or bool(_DAILY_MIX_NAME.match(text))


def playlist_share_url(playlist_id: str) -> str:
    return f"https://open.spotify.com/playlist/{playlist_id}"


def fetch_embed_playlist(playlist_id: str, *, session: requests.Session | None = None) -> dict[str, Any]:
    """Read a shareable playlist from Spotify's public embed page."""
    client = session or requests.Session()
    try:
        response = client.get(
            EMBED_PLAYLIST_URL.format(playlist_id=playlist_id),
            headers=EMBED_HEADERS,
            timeout=25,
        )
    except requests.RequestException as exc:
        raise SpotifyError(f"Could not open the Spotify share page: {exc}") from exc
    if response.status_code >= 400:
        raise SpotifyError(f"Spotify share page returned HTTP {response.status_code}.")
    entity = _embed_entity(response.text)
    name = str(entity.get("name") or entity.get("title") or "Spotify Playlist").strip()
    tracks: list[dict[str, Any]] = []
    for item in entity.get("trackList") or []:
        track = _track_from_embed_entry(item, name, len(tracks) + 1)
        if track:
            tracks.append(track)
    if not tracks:
        raise SpotifyError(f"{name}: the public share page did not include any songs.")
    owner = str(entity.get("subtitle") or "")
    return {
        "id": str(entity.get("id") or playlist_id),
        "name": name,
        "url": playlist_share_url(playlist_id),
        "tracks": tracks,
        "snapshot": [str(track["sp_id"]) for track in tracks],
        "from_share_link": True,
        "owner": owner,
        "made_for_you": is_made_for_you_playlist(name, "spotify" if owner.casefold() == "spotify" else ""),
        "total": len(tracks),
    }


def _embed_entity(html: str) -> dict[str, Any]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        raise SpotifyError("Could not read songs from the Spotify share page.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SpotifyError("Spotify share page returned invalid playlist data.") from exc
    entity = (((payload.get("props") or {}).get("pageProps") or {}).get("state") or {}).get("data", {}).get("entity")
    if not isinstance(entity, dict):
        raise SpotifyError("Spotify share page did not include playlist details.")
    return entity


def _track_from_embed_entry(item: Any, playlist_name: str, position: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    uri = str(item.get("uri") or "")
    if not uri.startswith("spotify:track:"):
        return None
    title = str(item.get("title") or "").strip()
    artists = str(item.get("subtitle") or "").strip()
    if not title or not artists:
        return None
    return {
        "title": title,
        "artists": artists,
        "album": "",
        "playlist": playlist_name,
        "isrc": None,
        "sp_id": uri.rsplit(":", 1)[-1],
        "duration_ms": int(item.get("duration") or item.get("duration_ms") or 0),
        "year": None,
        "cover_url": None,
        "track_no": position,
        "disc_no": 1,
    }


def saved_playlist_entry(item: Any, *, selected: bool | None = None) -> dict[str, Any]:
    if isinstance(item, dict):
        playlist_id = str(item.get("id") or "")
        name = str(item.get("name") or playlist_id)
        url = str(item.get("url") or playlist_share_url(playlist_id))
        enabled = bool(item.get("enabled", True) if selected is None else selected)
        pinned = bool(item.get("pinned"))
        owner_id = str(item.get("owner_id") or "")
        total = item.get("total", "")
        owned = item.get("owned")
    else:
        playlist_id = str(getattr(item, "id", "") or "")
        name = str(getattr(item, "name", "") or playlist_id)
        url = str(getattr(item, "url", "") or playlist_share_url(playlist_id))
        enabled = bool(getattr(item, "enabled", True) if selected is None else selected)
        pinned = bool(getattr(item, "pinned", False))
        owner_id = ""
        total = ""
        owned = None
    return {
        "id": playlist_id,
        "name": name,
        "url": url,
        "total": total,
        "selected": enabled,
        "pinned": pinned,
        "owned": owned,
        "made_for_you": is_made_for_you_playlist(name, owner_id),
        "from_share_link": pinned,
    }


def merge_saved_playlists(remote: list[dict[str, Any]], saved: list[Any]) -> list[dict[str, Any]]:
    """Keep URL-added / Mix playlists visible after a library refresh."""
    merged = list(remote)
    seen = {str(item.get("id") or "") for item in merged}
    selected = {}
    for item in saved:
        pid = str(getattr(item, "id", "") or "")
        if pid:
            selected[pid] = bool(getattr(item, "enabled", True))
    for item in merged:
        pid = str(item.get("id") or "")
        if pid in selected:
            item["selected"] = selected[pid]
    for item in saved:
        pid = str(getattr(item, "id", "") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        merged.append(saved_playlist_entry(item))
    return merged


def _playlist_item_total(item: dict[str, Any]) -> int:
    for key in ("items", "tracks"):
        payload = item.get(key)
        if isinstance(payload, dict) and payload.get("total") is not None:
            try:
                return int(payload.get("total") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _playlist_sort_key(playlist: dict[str, Any]) -> tuple[int, str]:
    name = str(playlist.get("name") or "").casefold()
    if playlist.get("made_for_you"):
        return (0, name)
    if playlist.get("owned"):
        return (1, name)
    return (2, name)


def _spotify_http_error(response: requests.Response, path: str) -> str:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif error:
            detail = str(error)
        detail = detail or str(payload.get("error_description") or "")
    if response.status_code == 403:
        return (
            detail
            or "Spotify denied access to this playlist. Developer apps can only read songs from playlists you own."
        )
    if detail:
        return f"Spotify API returned HTTP {response.status_code} for {path}: {detail}"
    return f"Spotify API returned HTTP {response.status_code}."


def parse_playlist_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise SpotifyError("Paste a Spotify playlist link first.")
    if is_liked_songs_id(text) or text.rstrip("/").endswith("/collection/tracks"):
        return LIKED_SONGS_ID
    parsed = urlparse(text)
    if parsed.netloc in {"open.spotify.com", "play.spotify.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parts[:2] == ["collection", "tracks"]:
            return LIKED_SONGS_ID
    if text.startswith("spotify:playlist:"):
        return text.split(":")[-1]
    if parsed.netloc in {"open.spotify.com", "play.spotify.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "playlist":
            return parts[1].split("?")[0]
    if all(ch.isalnum() for ch in text) and 16 <= len(text) <= 32:
        return text
    raise SpotifyError("Paste a Spotify playlist URL.")


def _user_playlist(item: Any, current_user_id: str = "") -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    playlist_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not playlist_id or not name:
        return None
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    owner_id = str(owner.get("id") or "")
    urls = item.get("external_urls") if isinstance(item.get("external_urls"), dict) else {}
    owned = bool(current_user_id) and owner_id == current_user_id
    return {
        "id": playlist_id,
        "name": name,
        "owner": str(owner.get("display_name") or owner_id or "Unknown"),
        "owner_id": owner_id,
        "total": _playlist_item_total(item),
        "collaborative": bool(item.get("collaborative")),
        "public": item.get("public"),
        "url": str(urls.get("spotify") or f"https://open.spotify.com/playlist/{playlist_id}"),
        "owned": owned,
        "made_for_you": is_made_for_you_playlist(name, owner_id),
    }


def _track_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("item", "track"):
        data = item.get(key)
        if isinstance(data, dict):
            return data
    if item.get("type") == "track" and item.get("name"):
        return item
    return None


def _track_from_item(item: Any, playlist_name: str, position: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    data = _track_payload(item)
    if not isinstance(data, dict) or data.get("type") not in (None, "track"):
        return None
    if data.get("is_local"):
        title = str(data.get("name") or "").strip()
        artists = ", ".join(
            str(artist.get("name") or "").strip()
            for artist in (data.get("artists") or [])
            if isinstance(artist, dict) and artist.get("name")
        )
        if not title:
            return None
        return {
            "title": title,
            "artists": artists or "Unknown Artist",
            "album": "",
            "playlist": playlist_name,
            "isrc": None,
            "sp_id": track_key_from_local(title, artists, int(data.get("duration_ms") or 0)),
            "duration_ms": int(data.get("duration_ms") or 0),
            "year": None,
            "cover_url": None,
            "track_no": position,
            "disc_no": 1,
        }
    title = str(data.get("name") or "").strip()
    artists = ", ".join(
        str(artist.get("name") or "").strip()
        for artist in (data.get("artists") or [])
        if isinstance(artist, dict) and artist.get("name")
    )
    if not title or not artists:
        return None
    album = data.get("album") if isinstance(data.get("album"), dict) else {}
    images = album.get("images") if isinstance(album.get("images"), list) else []
    cover_url = next((str(image.get("url")) for image in images if isinstance(image, dict) and image.get("url")), None)
    external_ids = data.get("external_ids") if isinstance(data.get("external_ids"), dict) else {}
    release_date = str(album.get("release_date") or "")
    return {
        "title": title,
        "artists": artists,
        "album": str(album.get("name") or ""),
        "playlist": playlist_name,
        "isrc": str(external_ids.get("isrc") or "") or None,
        "sp_id": str(data.get("id") or ""),
        "duration_ms": int(data.get("duration_ms") or 0),
        "year": int(release_date[:4]) if release_date[:4].isdigit() else None,
        "cover_url": cover_url,
        "track_no": int(data.get("track_number") or position),
        "disc_no": int(data.get("disc_number") or 1),
    }


def track_key_from_local(title: str, artists: str, duration_ms: int) -> str:
    return f"local:{title.strip().casefold()}|{artists.strip().casefold()}|{int(duration_ms)}"
