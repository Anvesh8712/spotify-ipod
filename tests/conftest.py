from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Keep support-dir I/O inside the test temp tree."""
    home = tmp_path / "app-home"
    home.mkdir()
    monkeypatch.setenv("SPOTIFY_IPOD_HOME", str(home))
    logger = logging.getLogger("spotify_ipod")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    logger.handlers.clear()
    return home
