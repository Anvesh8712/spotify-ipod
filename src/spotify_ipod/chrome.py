from __future__ import annotations

import subprocess
import sys
from urllib.parse import urlparse


class BrowserError(RuntimeError):
    pass


def open_in_chrome(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserError("Refusing to open a non-HTTP authorization URL.")
    if sys.platform == "darwin":
        cmd = ["open", "-a", "Google Chrome", url]
    else:
        cmd = ["google-chrome", url]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        raise BrowserError(detail or "Could not open Google Chrome. Is it installed?") from exc
