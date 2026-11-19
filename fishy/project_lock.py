"""Warn when a project is already open elsewhere (FR-7.7).

While a project is open, fishy holds a small lock file next to it:
``investigation.fishy.lock`` (hidden on Windows). It records who has the file
open, so a second window, a second copy of fishy, or a colleague opening the
same file on a shared drive can be warned. Two writers wouldn't corrupt the file
(SQLite serializes them), but each would show stale data and undo would fight.

A lock left behind by a crash is cleaned up automatically when its process is
gone (same computer). One from another computer that crashed has to be
overridden with "Open anyway"; deleting the lock file by hand also works.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLockFile


def lock_path(project: str | Path) -> Path:
    p = Path(project)
    return p.with_name(p.name + ".lock")


@dataclass(frozen=True)
class LockHolder:
    pid: int
    hostname: str

    def describe(self) -> str:
        where = f"on {self.hostname}" if self.hostname else "on another computer"
        return f"{where} (process {self.pid})"


class ProjectLock:
    def __init__(self, project: str | Path) -> None:
        self.path = lock_path(project)
        self._lock = QLockFile(str(self.path))
        self._lock.setStaleLockTime(0)  # only the process check decides "stale"

    def acquire(self) -> bool:
        """True if we now hold the lock; False if someone else does."""
        try:
            ok = self._lock.tryLock(0)
        except Exception:  # e.g. read-only folder: don't block opening
            return True
        if ok:
            _hide(self.path)
            return True
        err = self._lock.error()
        # No permission to create the lock (read-only share): proceed without one.
        return err == QLockFile.LockError.PermissionError

    def holder(self) -> LockHolder | None:
        pid, host, _app = self._lock.getLockInfo()  # app is just the executable name
        return LockHolder(int(pid), str(host)) if pid else None

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()

    @property
    def held(self) -> bool:
        return self._lock.isLocked()


def _hide(path: Path) -> None:
    """Mark the lock file hidden in Explorer, like Office's ~$ files."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass
