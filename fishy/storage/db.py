"""Opening, creating and migrating `.fishy` project files (FR-7.1, FR-7.2, FR-7.4).

A project file *is* a SQLite database. Every edit is committed immediately, so
there is no "save". The rollback-journal mode (`journal_mode=DELETE`) with
`synchronous=FULL` keeps the file consistent if the app is killed mid-write
(NFR-3), and leaves no `-wal`/`-shm` side files, so the `.fishy` file can be
copied or emailed even while it is open.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

PROJECT_SUFFIX = ".fishy"
APPLICATION_ID = 0x46534859  # "FSHY": marks the file as a fishy project


def _migrations() -> list[tuple[int, str]]:
    """(version, sql) pairs from storage/schema/NNN_*.sql, in order."""
    out = []
    for entry in resources.files("fishy.storage.schema").iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:3].isdigit():
            out.append((int(name[:3]), entry.read_text(encoding="utf-8")))
    return sorted(out)


def latest_version() -> int:
    return _migrations()[-1][0]


class NotAProjectError(Exception):
    pass


class NewerSchemaError(Exception):
    pass


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection configured for write-through use.

    `isolation_level=None` puts sqlite3 in autocommit mode; the repository
    opens explicit `BEGIN IMMEDIATE` transactions for each edit.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, path: str | Path | None = None) -> list[int]:
    """Bring the schema up to date. Returns the versions applied.

    For an existing file older than the current schema, a backup copy
    (`name.fishy.bak-v<N>`) is made first (FR-7.4).
    """
    current = schema_version(conn)
    pending = [(v, sql) for v, sql in _migrations() if v > current]
    if current > latest_version():
        raise NewerSchemaError(
            f"This file uses schema v{current}; this version of fishy supports up to "
            f"v{latest_version()}. Please update fishy."
        )
    if not pending:
        return []
    if current > 0 and path is not None:
        backup_to(conn, Path(f"{path}.bak-v{current}"))
    applied = []
    for version, sql in pending:
        # executescript() commits first; wrap the script in its own transaction.
        conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        applied.append(version)
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    return applied


def check_is_project(conn: sqlite3.Connection) -> None:
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    version = schema_version(conn)
    if version == 0:
        # Empty database is fine (new file); anything else with tables is not ours.
        tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        if tables:
            raise NotAProjectError("This SQLite file is not a fishy project.")
    elif app_id != APPLICATION_ID:
        raise NotAProjectError("This SQLite file is not a fishy project.")


def backup_to(conn: sqlite3.Connection, dest: str | Path) -> None:
    """Consistent copy of the open project via SQLite's online backup API (FR-7.2)."""
    dest = Path(dest)
    if dest.exists():
        dest.unlink()
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
