from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from spotify_ipod.paths import applescript_path, support_dir

PLAYLIST_MARKER_PREFIX = "spotify-ipod:"
TRACK_COMMENT_PREFIX = "spotify-ipod:"


class MusicError(RuntimeError):
    pass


@dataclass
class ImportResult:
    persistent_id: str
    file_path: str = ""


def playlist_marker(spotify_playlist_id: str) -> str:
    return f"{PLAYLIST_MARKER_PREFIX}{spotify_playlist_id}"


def track_comment(sp_id: str) -> str:
    return f"{TRACK_COMMENT_PREFIX}{sp_id}"


def auto_add_directory() -> Path | None:
    candidates = [
        Path.home() / "Music/Music/Media.localized/Automatically Add to Music.localized",
        Path.home() / "Music/Music/Media/Automatically Add to Music.localized",
        Path.home() / "Music/iTunes/iTunes Media/Automatically Add to iTunes.localized",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return None


def is_app_managed_path(path: Path) -> bool:
    """True for staging/library copies we own and can delete after Music imports."""
    try:
        resolved = path.expanduser().resolve()
        root = support_dir().resolve()
        return root in resolved.parents or resolved.parent == root
    except OSError:
        return False


def run_music_bridge(command: str, *args: str, script_path: Path | None = None, timeout: int | None = None) -> str:
    script = script_path or applescript_path()
    cmd = ["osascript", str(script), command, *args]
    if timeout is None:
        if command == "rebuild-playlist":
            timeout = 180
        elif command == "convert-aac":
            timeout = 600
        elif command == "import":
            timeout = 120
        else:
            timeout = 45
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MusicError(
            "Music.app did not respond. Click Music in the Dock in case a dialog is waiting, then Sync again."
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Music AppleScript failed.").strip()
        raise MusicError(detail)
    return (result.stdout or "").strip()


class MusicLibrary:
    def __init__(self, runner=run_music_bridge) -> None:
        self._run = runner

    def ping(self) -> str:
        return self._run("ping")

    def find_by_comment(self, marker: str) -> str:
        if not marker:
            return ""
        return self._run("find-comment", marker)

    def find_all_by_comment(self, marker: str) -> list[str]:
        if not marker:
            return []
        raw = self._run("find-comments", marker)
        return [part for part in raw.split(",") if part]

    def track_location(self, persistent_id: str) -> str:
        if not persistent_id:
            return ""
        return self._run("track-location", persistent_id)

    def import_file(self, path: Path, marker: str = "") -> ImportResult:
        """Import once via Music's add command.

        Do not also drop the file into Automatically Add — that raced with add and
        created duplicate library tracks when Copy-to-Media-folder was enabled.
        """
        path = Path(path)
        if marker:
            existing_ids = self.find_all_by_comment(marker)
            if existing_ids:
                keep = existing_ids[0]
                for extra in existing_ids[1:]:
                    try:
                        self.delete_track(extra)
                    except MusicError:
                        pass
                location = self.track_location(keep)
                self._discard_staging_copy(path, location)
                return ImportResult(persistent_id=keep, file_path=location or str(path))

        if not path.exists():
            raise MusicError(f"Cannot import missing file: {path}")

        try:
            persistent_id = self._run("import", str(path))
        except MusicError:
            if marker:
                persistent_id = self.find_by_comment(marker)
            else:
                persistent_id = ""
            if not persistent_id:
                raise

        if not persistent_id and marker:
            # Music may finish copying before returning; poll briefly.
            deadline = time.time() + 15
            while time.time() < deadline and not persistent_id:
                time.sleep(0.4)
                persistent_id = self.find_by_comment(marker)

        if not persistent_id:
            raise MusicError(f"Music did not return a persistent ID for {path}.")

        # If a race still created duplicates, keep the first and delete the rest.
        if marker:
            for extra in self.find_all_by_comment(marker):
                if extra != persistent_id:
                    try:
                        self.delete_track(extra)
                    except MusicError:
                        pass

        location = ""
        deadline = time.time() + 20
        while time.time() < deadline:
            location = self.track_location(persistent_id)
            if location and Path(location).exists():
                break
            time.sleep(0.4)

        self._discard_staging_copy(path, location)
        return ImportResult(persistent_id=persistent_id, file_path=location or str(path))

    def _discard_staging_copy(self, path: Path, music_location: str) -> None:
        if not path.exists():
            return
        try:
            if music_location:
                music_path = Path(music_location)
                if music_path.exists() and music_path.resolve() == path.resolve():
                    return
            if is_app_managed_path(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def track_exists(self, persistent_id: str) -> bool:
        if not persistent_id:
            return False
        return self._run("track-exists", persistent_id).lower() == "yes"

    def ensure_playlist(self, name: str, spotify_playlist_id: str) -> str:
        persistent_id = self._run("ensure-playlist", name, playlist_marker(spotify_playlist_id))
        if not persistent_id:
            raise MusicError(f"Music did not create playlist {name}.")
        return persistent_id

    def rebuild_playlist(self, playlist_persistent_id: str, track_persistent_ids: list[str]) -> None:
        joined = ",".join(track_persistent_ids)
        self._run("rebuild-playlist", playlist_persistent_id, joined)

    def delete_track(self, persistent_id: str) -> None:
        if persistent_id:
            self._run("delete-track", persistent_id)

    def set_comment(self, persistent_id: str, marker: str) -> None:
        if persistent_id and marker:
            self._run("set-comment", persistent_id, marker)

    def convert_to_aac(self, persistent_id: str) -> ImportResult:
        """Convert a library track with Music's AAC Encoder (not ffmpeg)."""
        if not persistent_id:
            raise MusicError("Cannot convert a track without a persistent ID.")
        new_pid = self._run("convert-aac", persistent_id)
        if not new_pid:
            raise MusicError(f"Music AAC convert failed for {persistent_id}.")
        location = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            location = self.track_location(new_pid)
            if location and Path(location).exists():
                break
            time.sleep(0.4)
        return ImportResult(persistent_id=new_pid, file_path=location)
