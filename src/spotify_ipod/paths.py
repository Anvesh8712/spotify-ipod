from __future__ import annotations

import os
import pathlib
import sys


APP_NAME = "spotify-ipod"
BUNDLE_ID = "com.spotify-ipod.sync"


def home() -> pathlib.Path:
    return pathlib.Path.home()


def support_dir() -> pathlib.Path:
    override = os.environ.get("SPOTIFY_IPOD_HOME")
    if override:
        path = pathlib.Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    if sys.platform == "darwin":
        path = home() / "Library" / "Application Support" / APP_NAME
    else:
        path = home() / f".{APP_NAME}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> pathlib.Path:
    return support_dir() / "config.json"


def state_path() -> pathlib.Path:
    return support_dir() / "state.json"


def unmatched_path() -> pathlib.Path:
    return support_dir() / "unmatched.json"


def lock_path() -> pathlib.Path:
    return support_dir() / "sync.lock"


def log_dir() -> pathlib.Path:
    path = support_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir() -> pathlib.Path:
    path = support_dir() / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_dir(configured: str | None = None) -> pathlib.Path:
    if configured:
        path = pathlib.Path(configured).expanduser()
    else:
        path = support_dir() / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def package_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent


def applescript_path() -> pathlib.Path:
    return package_dir() / "music_bridge.applescript"


def launch_agent_label() -> str:
    return BUNDLE_ID


def launch_agent_plist_path() -> pathlib.Path:
    return home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
