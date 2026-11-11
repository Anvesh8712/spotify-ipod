from __future__ import annotations

import argparse
import sys
from typing import Sequence

from spotify_ipod.config import PlaylistConfig, load_config, save_config
from spotify_ipod.lock import SyncLock, SyncLockError
from spotify_ipod.logging_setup import get_logger, setup_logging
from spotify_ipod.paths import config_path, launch_agent_plist_path, state_path
from spotify_ipod.pipeline import SyncPipeline
from spotify_ipod.scheduler import agent_installed, install_agent, uninstall_agent
from spotify_ipod.spotify import SpotifyClient, SpotifyError, parse_playlist_id
from spotify_ipod.state import load_state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spotify-ipod",
        description="Sync Spotify playlists into Music.app as iPod-safe AAC tracks.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="Open the setup window.")
    sub.add_parser("login", help="Sign in to Spotify in Google Chrome.")
    sub.add_parser("playlists", help="List Spotify playlists and print IDs.")
    sub.add_parser("status", help="Show config, auth, and last snapshot.")

    sync = sub.add_parser("sync", help="Scan selected playlists and update Music.app.")
    sync.add_argument("--dry-run", action="store_true", help="Show changes without downloading or importing.")

    add = sub.add_parser("add-playlist", help="Watch a Spotify playlist URL.")
    add.add_argument("url")
    add.add_argument("--name", default="", help="Music.app playlist name override.")

    sub.add_parser("install-agent", help="Install the scheduled macOS LaunchAgent.")
    sub.add_parser("uninstall-agent", help="Remove the scheduled LaunchAgent.")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        args.command = "setup"
    setup_logging()
    handlers = {
        "setup": cmd_setup,
        "login": cmd_login,
        "playlists": cmd_playlists,
        "status": cmd_status,
        "sync": cmd_sync,
        "add-playlist": cmd_add_playlist,
        "install-agent": cmd_install_agent,
        "uninstall-agent": cmd_uninstall_agent,
    }
    try:
        return handlers[args.command](args)
    except (SpotifyError, SyncLockError) as exc:
        get_logger().error("%s", exc)
        return 1


def cmd_setup(_args: argparse.Namespace) -> int:
    from spotify_ipod.app import launch_setup

    launch_setup()
    return 0


def cmd_login(_args: argparse.Namespace) -> int:
    config = load_config()
    if not config.spotify_client_id:
        print("Save a Spotify Client ID first. Create an app at https://developer.spotify.com/dashboard")
        print(f"Redirect URI must be exactly: {config.redirect_uri}")
        print(f"Then put the Client ID in {config_path()}")
        return 1
    client = SpotifyClient(config.spotify_client_id, config.redirect_uri)
    client.get_access_token(interactive=True)
    me = client.current_user()
    print(f"Signed in as {me.get('display_name') or me.get('id')}")
    return 0


def cmd_playlists(_args: argparse.Namespace) -> int:
    config = load_config()
    client = SpotifyClient(config.spotify_client_id, config.redirect_uri)
    for playlist in client.list_playlists():
        mark = "*" if any(item.id == playlist["id"] and item.enabled for item in config.playlists) else " "
        print(f"[{mark}] {playlist['name']}  {playlist['total']} tracks  {playlist['url']}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    config = load_config()
    state = load_state()
    print(f"Config: {config_path()}")
    print(f"State:  {state_path()}")
    print(f"Client ID: {'set' if config.spotify_client_id else 'missing'}")
    print(f"Output: {config.resolved_output_dir()}")
    print(f"AAC: {config.aac_bitrate} {config.sample_rate} Hz {config.channels} ch")
    print(f"Schedule: every {config.schedule_hours}h  agent={'installed' if agent_installed() else 'not installed'}")
    if agent_installed():
        print(f"Agent plist: {launch_agent_plist_path()}")
    print("Playlists:")
    if not config.playlists:
        print("  (none selected)")
    for playlist in config.playlists:
        record = state.playlists.get(playlist.id)
        snapshot = len(record.snapshot) if record else 0
        print(f"  [{'on' if playlist.enabled else 'off'}] {playlist.display_name}  snapshot={snapshot}")
    print(f"Cached tracks: {len(state.tracks)}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    config = load_config()
    if not config.spotify_client_id:
        print("Missing Spotify Client ID. Run: spotify-ipod setup")
        return 1
    try:
        with SyncLock():
            report = SyncPipeline(config).run(dry_run=bool(args.dry_run))
    except SyncLockError as exc:
        print(exc)
        return 1
    print(report.summary())
    return 1 if report.errors else 0


def cmd_add_playlist(args: argparse.Namespace) -> int:
    config = load_config()
    playlist_id = parse_playlist_id(args.url)
    client = SpotifyClient(config.spotify_client_id, config.redirect_uri)
    data = client.fetch_playlist(playlist_id)
    existing = next((item for item in config.playlists if item.id == playlist_id), None)
    if existing:
        existing.enabled = True
        existing.name = data["name"]
        existing.url = data["url"]
        existing.pinned = True
        if args.name:
            existing.music_name = args.name
    else:
        config.playlists.append(
            PlaylistConfig(
                id=playlist_id,
                name=data["name"],
                url=data["url"],
                enabled=True,
                music_name=args.name or None,
                pinned=True,
            )
        )
    save_config(config)
    print(f"Watching {data['name']} ({len(data['tracks'])} tracks)")
    return 0


def cmd_install_agent(_args: argparse.Namespace) -> int:
    path = install_agent(load_config())
    print(f"Installed LaunchAgent: {path}")
    print("macOS may ask for Automation permission for Music the first time it runs.")
    return 0


def cmd_uninstall_agent(_args: argparse.Namespace) -> int:
    uninstall_agent()
    print("Removed LaunchAgent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
