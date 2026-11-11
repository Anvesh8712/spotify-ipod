from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from spotify_ipod.config import AppConfig
from spotify_ipod.paths import BUNDLE_ID, launch_agent_plist_path, package_dir


class SchedulerError(RuntimeError):
    pass


def sync_executable() -> list[str]:
    env_bin = os.environ.get("SPOTIFY_IPOD_BIN")
    if env_bin:
        return [env_bin, "sync"]
    app = _find_app_binary()
    if app:
        return [str(app), "sync"]
    script = shutil.which("spotify-ipod")
    if script:
        return [script, "sync"]
    return [sys.executable, "-m", "spotify_ipod", "sync"]


def _find_app_binary() -> Path | None:
    candidates = [
        Path.home() / "Applications" / "SpotifyToMusic.app" / "Contents" / "MacOS" / "SpotifyToMusic",
        Path("/Applications/SpotifyToMusic.app/Contents/MacOS/SpotifyToMusic"),
        Path.home() / "Applications" / "SpotifyIpod.app" / "Contents" / "MacOS" / "spotify-ipod",
        Path("/Applications/SpotifyIpod.app/Contents/MacOS/spotify-ipod"),
    ]
    here = package_dir().parents[1]
    candidates.append(here / "dist" / "SpotifyToMusic.app" / "Contents" / "MacOS" / "SpotifyToMusic")
    candidates.append(here / "dist" / "SpotifyIpod.app" / "Contents" / "MacOS" / "spotify-ipod")
    for path in candidates:
        if path.exists():
            return path
    return None


def launch_agent_plist(config: AppConfig, executable: list[str] | None = None) -> dict:
    hours = max(1, int(config.schedule_hours or 6))
    program = executable or sync_executable()
    return {
        "Label": BUNDLE_ID,
        "ProgramArguments": program,
        "StartInterval": hours * 3600,
        "RunAtLoad": False,
        "Nice": 10,
        "StandardOutPath": str(Path.home() / "Library" / "Logs" / "spotify-ipod.out.log"),
        "StandardErrorPath": str(Path.home() / "Library" / "Logs" / "spotify-ipod.err.log"),
    }


def install_agent(config: AppConfig, *, executable: list[str] | None = None) -> Path:
    if sys.platform != "darwin":
        raise SchedulerError("LaunchAgents are only supported on macOS.")
    plist_path = launch_agent_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = launch_agent_plist(config, executable)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False, capture_output=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already bootstrapped" not in (result.stderr or ""):
        raise SchedulerError(result.stderr.strip() or "Could not install the LaunchAgent.")
    return plist_path


def uninstall_agent() -> None:
    plist_path = launch_agent_plist_path()
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{BUNDLE_ID}"], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False, capture_output=True)
    plist_path.unlink(missing_ok=True)


def agent_installed() -> bool:
    return launch_agent_plist_path().exists()
