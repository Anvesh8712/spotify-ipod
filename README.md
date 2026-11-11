# Spotify → Music (iPod sync)

macOS app that syncs Spotify playlists into Music.app for iPod-friendly playback.

## What it does

1. Pulls playlists from your Spotify library (plus pinned share URLs)
2. Downloads missing tracks with **CSVMusic 1.7.7**
3. Imports into Music.app and converts with Music’s **AAC Encoder**
4. Rebuilds Music playlists to match Spotify

## Requirements

- macOS 13+
- Anaconda/Miniconda Python (Apple’s Command Line Tools Tk crashes on macOS 15)
- Spotify Developer app Client ID
- CSVMusic 1.7.7 wheel (installed by the build script)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install --no-deps https://github.com/angall1/CSVMusic/releases/download/v1.7.7/csvmusic-1.7.7-py3-none-any.whl
```

Run the setup UI:

```bash
python -m spotify_ipod setup
```

Or build a local `.app`:

```bash
bash scripts/build_app.sh
open ~/Applications/SpotifyToMusic.app
```

## Finding and syncing playlists

Open **Search Spotify**, enter a playlist name, and choose **Add to sync** on a result. Search uses your existing Spotify sign-in. Search includes a small list of Spotify editorial playlists, such as Bedroom Pop, that can be missing from the developer API. Use **Open Spotify search** to look for other editorial playlists, then add their share links from Library.

Sync can download up to three missing tracks at once. Music.app imports and playlist updates run in order.

## Unmatched songs

If CSVMusic cannot confidently match a track, **Fix unmatched…** opens with suggested YouTube candidates (score + title). Pick one or paste your own URL. Overrides are stored at:

`~/Library/Application Support/spotify-ipod/overrides.json`

## Notes

- Tokens live in the macOS Keychain; config/state live under Application Support
- Songs stay in Music even after they leave a watched playlist
