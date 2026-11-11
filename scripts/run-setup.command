#!/bin/bash
# Double-click or: open scripts/run-setup.command
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/bin:$PATH"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/src"
if [[ -d "$HOME/miniconda3/lib/tcl8.6" ]]; then
  export TCL_LIBRARY="$HOME/miniconda3/lib/tcl8.6"
  export TK_LIBRARY="$HOME/miniconda3/lib/tk8.6"
fi
exec "$ROOT/.venv/bin/python" -m spotify_ipod setup
