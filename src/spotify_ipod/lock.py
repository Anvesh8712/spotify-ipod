from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from spotify_ipod.paths import lock_path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class SyncLockError(RuntimeError):
    pass


class SyncLock:
    """Prevent overlapping scheduled and manual syncs."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or lock_path()
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._handle.close()
                self._handle = None
                raise SyncLockError("A sync is already running.") from exc
        else:
            try:
                os.write(self._handle.fileno(), b"0")
            except OSError as exc:
                self._handle.close()
                self._handle = None
                raise SyncLockError("A sync is already running.") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()

    def release(self) -> None:
        if self._handle is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "SyncLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
