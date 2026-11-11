#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Spotify to Music"
BUNDLE_NAME="SpotifyToMusic"
DIST="$ROOT/dist"
APP="$DIST/$BUNDLE_NAME.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
VENV="$RESOURCES/venv"
INSTALL_DIR="${SPOTIFY_IPOD_INSTALL_DIR:-$HOME/Applications}"

# Prefer the project venv's base interpreter (Anaconda/Miniconda). Apple's
# Command Line Tools Tk aborts on macOS 15.
PY="${ROOT}/.venv/bin/python"
if [[ -x "$PY" ]]; then
  PY="$("$PY" -c 'import sys; print(sys.base_prefix)')/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  PY="${HOME}/miniconda3/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "Need Anaconda/Miniconda Python. Apple's python3 Tk crashes on macOS 15." >&2
  exit 1
fi

TCL_DIR=""
TK_DIR=""
BASE_PREFIX="$("$PY" -c 'import sys; print(sys.base_prefix)')"
if [[ -d "$BASE_PREFIX/lib/tcl8.6" ]]; then
  TCL_DIR="$BASE_PREFIX/lib/tcl8.6"
  TK_DIR="$BASE_PREFIX/lib/tk8.6"
fi

# Prefer move-aside over rm -rf: sandboxed builds often cannot delete files
# that already live under a .app bundle (com.apple.provenance).
if [[ -e "$APP" ]]; then
  trash="$DIST/${BUNDLE_NAME}.app.prev.$$"
  rm -rf "$trash" 2>/dev/null || true
  mv "$APP" "$trash"
  rm -rf "$trash" 2>/dev/null || true
fi
mkdir -p "$MACOS" "$RESOURCES"

# Create the venv outside the .app first. Some sandboxed environments block
# python -m venv directly under Contents/Resources/*.app paths.
VENV_STAGE="$DIST/${BUNDLE_NAME}-venv-stage"
rm -rf "$VENV_STAGE"
echo "Creating app venv with $PY …"
"$PY" -m venv "$VENV_STAGE"
"$VENV_STAGE/bin/pip" install --upgrade pip
"$VENV_STAGE/bin/pip" install "$ROOT"
# CSVMusic provides the download/match path; skip its GUI (PySide6) dependency.
CSV_WHEEL_URL="https://github.com/angall1/CSVMusic/releases/download/v1.7.7/csvmusic-1.7.7-py3-none-any.whl"
"$VENV_STAGE/bin/pip" install --no-deps "$CSV_WHEEL_URL"
"$VENV_STAGE/bin/python" -c "import csvmusic; print('csvmusic', csvmusic.__version__ if hasattr(csvmusic,'__version__') else 'ok')"
mv "$VENV_STAGE" "$VENV"

# Verify Tk works with this interpreter before shipping the .app.
"$VENV/bin/python" - <<'PY'
import tkinter as tk
root = tk.Tk()
root.withdraw()
root.destroy()
print("tk ok")
PY

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleDisplayName</key>
	<string>${APP_NAME}</string>
	<key>CFBundleExecutable</key>
	<string>SpotifyToMusic</string>
	<key>CFBundleIdentifier</key>
	<string>com.spotify-ipod.sync</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>${APP_NAME}</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>0.2.0</string>
	<key>CFBundleVersion</key>
	<string>0.2.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>13.0</string>
	<key>LSUIElement</key>
	<false/>
	<key>NSAppleEventsUsageDescription</key>
	<string>Spotify to Music downloads via CSVMusic, converts with Music AAC Encoder, and rebuilds playlists for iPod sync.</string>
	<key>NSHighResolutionCapable</key>
	<true/>
</dict>
</plist>
PLIST

cat > "$MACOS/SpotifyToMusic" <<EOF
#!/bin/bash
set -euo pipefail
DIR="\$(cd "\$(dirname "\$0")" && pwd)"
ROOT="\$(cd "\$DIR/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export PYTHONUNBUFFERED=1
unset PYTHONPATH
if [[ -d "$TCL_DIR" ]]; then
  export TCL_LIBRARY="$TCL_DIR"
  export TK_LIBRARY="$TK_DIR"
fi
PY="\$ROOT/Resources/venv/bin/python"
if [[ "\${1:-}" == "" ]]; then
  exec "\$PY" -m spotify_ipod setup
fi
exec "\$PY" -m spotify_ipod "\$@"
EOF
chmod +x "$MACOS/SpotifyToMusic"

# Ad-hoc sign so macOS can attach TCC grants to a stable bundle id.
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
fi

mkdir -p "$INSTALL_DIR"
DEST="$INSTALL_DIR/$BUNDLE_NAME.app"
if [[ "$(cd "$INSTALL_DIR" && pwd)" != "$(cd "$DIST" && pwd)"* ]]; then
  # Install outside dist/ via move-aside + ditto (cp -R into .app can be blocked).
  if [[ -e "$DEST" ]]; then
    trash_dest="$INSTALL_DIR/${BUNDLE_NAME}.app.prev.$$"
    mv "$DEST" "$trash_dest" 2>/dev/null || true
    rm -rf "$trash_dest" 2>/dev/null || true
  fi
  if ditto "$APP" "$DEST" 2>/dev/null; then
    if command -v codesign >/dev/null 2>&1; then
      codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 || true
    fi
    echo "Installed $DEST"
  else
    echo "Warning: could not install to $DEST (open the dist copy instead)." >&2
    DEST="$APP"
  fi
else
  # Same-folder install target: just use the built app.
  DEST="$APP"
fi

echo "Built $APP"
echo "Installed $DEST"
echo "Open with Spotlight (Spotify to Music) or: open \"$DEST\""
echo "First launch may ask for Automation access to control Music."
