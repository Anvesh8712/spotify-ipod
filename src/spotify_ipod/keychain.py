from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

SERVICE = "com.spotify-ipod.spotify"
ACCOUNT = "tokens"


class KeychainError(RuntimeError):
    pass


def _security_bin() -> str:
    path = shutil.which("security")
    if not path:
        raise KeychainError("macOS Keychain is not available (security tool missing).")
    return path


def store_secret(payload: dict[str, Any], *, service: str = SERVICE, account: str = ACCOUNT) -> None:
    blob = json.dumps(payload, separators=(",", ":"))
    try:
        subprocess.run(
            [_security_bin(), "add-generic-password", "-s", service, "-a", account, "-w", blob, "-U"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise KeychainError(exc.stderr.strip() or "Could not store credentials in Keychain.") from exc


def load_secret(*, service: str = SERVICE, account: str = ACCOUNT) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [_security_bin(), "find-generic-password", "-s", service, "-a", account, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
    except KeychainError:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def delete_secret(*, service: str = SERVICE, account: str = ACCOUNT) -> None:
    subprocess.run(
        [_security_bin(), "delete-generic-password", "-s", service, "-a", account],
        check=False,
        capture_output=True,
        text=True,
    )
