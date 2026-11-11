from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from spotify_ipod.config import AppConfig, PlaylistConfig
from spotify_ipod.csvmusic_bridge import BatchDownloadResult, DownloadError, download_tracks, save_unmatched
from spotify_ipod.logging_setup import get_logger
from spotify_ipod.music import ImportResult, MusicError, MusicLibrary, track_comment
from spotify_ipod.paths import staging_dir
from spotify_ipod.spotify import SpotifyClient, SpotifyError
from spotify_ipod.state import (
    AppState,
    PlaylistRecord,
    TrackRecord,
    load_state,
    playlist_changed,
    save_state,
    track_key,
)


@dataclass
class SyncReport:
    dry_run: bool = False
    playlists: int = 0
    downloaded: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    rebuilt: list[str] = field(default_factory=list)
    removed_from_playlists: list[str] = field(default_factory=list)
    orphans_deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unmatched: list[dict[str, str]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'Dry run' if self.dry_run else 'Sync'} complete for {self.playlists} playlist(s).",
            f"Downloaded: {len(self.downloaded)}  Imported: {len(self.imported)}  Rebuilt: {len(self.rebuilt)}",
            f"Unchanged: {len(self.unchanged)}  Orphans deleted: {len(self.orphans_deleted)}  Errors: {len(self.errors)}  Unmatched: {len(self.unmatched)}",
        ]
        if self.errors:
            lines.extend(f"  - {error}" for error in self.errors)
        return "\n".join(lines)


class SyncPipeline:
    def __init__(
        self,
        config: AppConfig,
        *,
        spotify: SpotifyClient | None = None,
        music: MusicLibrary | None = None,
        state: AppState | None = None,
        prepare: Callable[..., dict[str, Any]] | None = None,
        download_missing: Callable[..., dict[str, Path]] | None = None,
        persist: Callable[[AppState], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.spotify = spotify or SpotifyClient(config.spotify_client_id, config.redirect_uri)
        self.music = music or MusicLibrary()
        self.state = state or load_state()
        # prepare: per-track injectable used by tests; production uses CSVMusic batch.
        self.prepare = prepare
        self.download_missing = download_missing or download_tracks
        self.persist = persist or save_state
        self.on_progress = on_progress or (lambda _msg: None)
        self.output_dir = Path(self.config.resolved_output_dir())
        self.logger = get_logger()
        self._pending_files: dict[str, Path] = {}

    def _note(self, message: str) -> None:
        self.logger.info("%s", message)
        self.on_progress(message)

    def run(self, *, dry_run: bool = False) -> SyncReport:
        report = SyncReport(dry_run=dry_run)
        selected = self.config.enabled_playlists()
        report.playlists = len(selected)
        if not selected:
            report.errors.append("No playlists selected.")
            return report

        fetched: dict[str, dict[str, Any]] = {}
        current_snapshots: dict[str, list[str]] = {}
        for playlist in selected:
            self._note(f"Fetching {playlist.display_name} from Spotify…")
            try:
                data = self.spotify.fetch_playlist(playlist.id)
            except SpotifyError as exc:
                report.errors.append(f"{playlist.display_name}: {exc}")
                continue
            fetched[playlist.id] = data
            current_snapshots[playlist.id] = list(data["snapshot"])
            self._note(f"Loaded {len(data['tracks'])} track(s) from {data['name']}.")

        for playlist in selected:
            data = fetched.get(playlist.id)
            if not data:
                continue
            self._sync_playlist(playlist, data, report, dry_run=dry_run)

        self._cleanup_orphans(current_snapshots, report, dry_run=dry_run)
        if not dry_run:
            self.persist(self.state)
            try:
                save_unmatched(report.unmatched)
            except OSError:
                pass
        return report

    def _sync_playlist(self, playlist: PlaylistConfig, data: dict[str, Any], report: SyncReport, *, dry_run: bool) -> None:
        snapshot = list(data["snapshot"])
        record = self.state.playlists.get(playlist.id) or PlaylistRecord(id=playlist.id, name=data["name"])
        music_name = playlist.display_name or data["name"]
        needs_rebuild = playlist_changed(record.snapshot, snapshot) or not record.music_persistent_id
        missing = [track for track in data["tracks"] if self._needs_file(track)]

        if not needs_rebuild and not missing:
            report.unchanged.append(music_name)
            return

        removed = [sp_id for sp_id in record.snapshot if sp_id not in snapshot]
        if removed:
            report.removed_from_playlists.extend(removed)

        if dry_run:
            for track in missing:
                report.downloaded.append(f"{track.get('artists')} - {track.get('title')}")
            if needs_rebuild:
                report.rebuilt.append(music_name)
            return

        self._pending_files = {}
        failed_keys: set[str] = set()
        if missing:
            if self.prepare is not None:
                # Test / injectable per-track path.
                for track in missing:
                    key = track_key(track)
                    label = f"{track.get('artists')} - {track.get('title')}"
                    try:
                        dest_dir = staging_dir() / "csvmusic"
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        prepared = self.prepare(track, dest_dir, self.config)
                        path = Path(prepared["path"])
                        self._pending_files[key] = path
                        report.downloaded.append(path.name)
                    except (DownloadError, OSError) as exc:
                        report.errors.append(f"{label}: {exc}")
                        report.skipped.append(label)
                        failed_keys.add(key)
            else:
                self._note(f"Downloading {len(missing)} missing track(s) with CSVMusic…")
                dest_dir = staging_dir() / "csvmusic"
                try:
                    raw = self.download_missing(
                        missing,
                        dest_dir,
                        music_name,
                        on_progress=self._note,
                    )
                except TypeError:
                    # Older injectable fakes may not accept on_progress.
                    raw = self.download_missing(missing, dest_dir, music_name)
                except DownloadError as exc:
                    report.errors.append(f"{music_name}: {exc}")
                    raw = {}
                if isinstance(raw, BatchDownloadResult):
                    found = raw.files
                    for item in raw.failed:
                        label = f"{item.get('artists')} - {item.get('title')}"
                        reason = item.get("reason") or "unmatched"
                        report.errors.append(f"{label}: {reason}")
                        report.skipped.append(label)
                        report.unmatched.append(item)
                        failed_keys.add(item["sp_id"])
                else:
                    found = raw
                self._pending_files.update(found)
                for path in found.values():
                    report.downloaded.append(path.name)
                for track in missing:
                    key = track_key(track)
                    if key not in found and key not in failed_keys:
                        label = f"{track.get('artists')} - {track.get('title')}"
                        item = {
                            "sp_id": key,
                            "title": str(track.get("title") or ""),
                            "artists": str(track.get("artists") or ""),
                            "reason": "no CSVMusic match/download",
                        }
                        report.errors.append(f"{label}: {item['reason']}")
                        report.skipped.append(label)
                        report.unmatched.append(item)
                        failed_keys.add(key)

        total = len(data["tracks"])
        for index, track in enumerate(data["tracks"], start=1):
            key = track_key(track)
            label = f"{track.get('artists')} - {track.get('title')}"
            self._note(f"[{index}/{total}] {label}")
            if key in failed_keys:
                continue
            try:
                self._ensure_imported(track, report)
            except (DownloadError, MusicError, OSError) as exc:
                report.errors.append(f"{label}: {exc}")
                report.skipped.append(label)
                stored = self.state.track(key)
                if stored and stored.music_persistent_id:
                    try:
                        if not self.music.track_exists(stored.music_persistent_id):
                            stored.music_persistent_id = ""
                            self.state.upsert_track(stored)
                    except MusicError:
                        stored.music_persistent_id = ""
                        self.state.upsert_track(stored)

        track_pids: list[str] = []
        for track in data["tracks"]:
            stored = self.state.track(track_key(track))
            if not stored or not stored.music_persistent_id:
                continue
            pid = stored.music_persistent_id
            try:
                exists = self.music.track_exists(pid)
            except MusicError:
                exists = False
            if not exists:
                label = f"{track.get('artists')} - {track.get('title')}"
                self._note(f"Clearing stale Music ID for {label}")
                stored.music_persistent_id = ""
                self.state.upsert_track(stored)
                continue
            track_pids.append(pid)

        if not track_pids:
            report.errors.append(f"{music_name}: no tracks were imported, so the Music playlist was not rebuilt.")
            record.snapshot = snapshot
            record.name = data["name"]
            record.music_name = music_name
            self.state.playlists[playlist.id] = record
            self.persist(self.state)
            return

        playlist_pid = self.music.ensure_playlist(music_name, playlist.id)
        self.music.rebuild_playlist(playlist_pid, track_pids)
        record.snapshot = snapshot
        record.name = data["name"]
        record.music_name = music_name
        record.music_persistent_id = playlist_pid
        self.state.playlists[playlist.id] = record
        report.rebuilt.append(music_name)
        self.persist(self.state)
        skipped = total - len(track_pids)
        if skipped:
            self._note(f"Rebuilt {music_name} with {len(track_pids)}/{total} tracks ({skipped} still missing).")
        else:
            self.logger.info("Rebuilt Music playlist %s (%s tracks)", music_name, len(track_pids))

    def _needs_file(self, track: dict[str, Any]) -> bool:
        stored = self.state.track(track_key(track))
        if stored is None:
            return True
        if stored.file_path and Path(stored.file_path).exists() and stored.music_persistent_id:
            try:
                if self.music.track_exists(stored.music_persistent_id):
                    return False
            except MusicError:
                return True
            return True
        return True

    def _ensure_imported(self, track: dict[str, Any], report: SyncReport) -> TrackRecord:
        key = track_key(track)
        marker = track_comment(key)
        stored = self.state.track(key)

        if stored and stored.music_persistent_id:
            try:
                alive = self.music.track_exists(stored.music_persistent_id)
            except MusicError:
                alive = False
            if alive:
                location = ""
                try:
                    location = self.music.track_location(stored.music_persistent_id)
                except MusicError:
                    location = stored.file_path
                if location and Path(location).exists():
                    if stored.file_path != location:
                        old = Path(stored.file_path) if stored.file_path else None
                        stored.file_path = location
                        self.state.upsert_track(stored)
                        self.persist(self.state)
                        if old and old.exists() and old.resolve() != Path(location).resolve():
                            try:
                                from spotify_ipod.music import is_app_managed_path

                                if is_app_managed_path(old):
                                    old.unlink(missing_ok=True)
                            except OSError:
                                pass
                    return stored
            else:
                # Music no longer has this track — drop the dead ID and re-import.
                stored.music_persistent_id = ""
                self.state.upsert_track(stored)
                self.persist(self.state)

        path = self._pending_files.get(key)
        if path is None or not Path(path).exists():
            if stored and stored.file_path and Path(stored.file_path).exists():
                path = Path(stored.file_path)
            else:
                raise DownloadError(f"No downloaded file available for {track.get('artists')} - {track.get('title')}")

        path = Path(path)
        try:
            imported = self.music.import_file(path, marker)
        except MusicError:
            # Stale comment match or hung import — retry once without reuse.
            imported = self.music.import_file(path, "")
        if isinstance(imported, ImportResult):
            persistent_id = imported.persistent_id
            location = imported.file_path or str(path)
        else:
            persistent_id = str(imported)
            location = str(path)

        # Tag before convert so we can find the track if convert is interrupted.
        try:
            self.music.set_comment(persistent_id, marker)
        except MusicError:
            pass

        # Apple AAC Encoder — same path that cleared static for the user.
        self._note(f"Music AAC convert (background): {track.get('title')}")
        converted = self.music.convert_to_aac(persistent_id)
        persistent_id = converted.persistent_id
        location = converted.file_path or location
        try:
            self.music.set_comment(persistent_id, marker)
        except MusicError:
            pass

        report.imported.append(Path(location).name if location else path.name)

        record = TrackRecord(
            sp_id=key,
            title=str(track.get("title") or ""),
            artists=str(track.get("artists") or ""),
            album=str(track.get("album") or ""),
            file_path=location or str(path),
            music_persistent_id=persistent_id,
            video_id="",
            duration_ms=int(track.get("duration_ms") or 0),
        )
        self.state.upsert_track(record)
        self.persist(self.state)
        return record

    def _cleanup_orphans(self, current_snapshots: dict[str, list[str]], report: SyncReport, *, dry_run: bool) -> None:
        # Keep downloaded songs in Music and on disk even after they leave every
        # watched playlist. Playlist membership is still updated by rebuild_playlist.
        _ = (current_snapshots, report, dry_run)
