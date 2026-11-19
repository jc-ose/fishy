"""The only code that touches SQL.

Every public mutating method runs in a transaction (nested calls share the
outer one), stamps `updated_at` / `updated_by` (FR-6.1, FR-6.4) and appends a
row to the `history` table (FR-6.3). It also enforces the structural
invariants:

* sibling `position`s are contiguous 0..n-1;
* top-level bones are categories, with no level, status, pin or log entries
  (FR-1a.4, Q14); every cause has a level and a status;
* a bone can't be moved into its own subtree.

Undo support: destructive operations return snapshots that `restore_*`
methods put back exactly, with the same ids and timestamps.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fishy.domain import (
    CATEGORY_TEMPLATES,
    DEFAULT_LEVELS,
    DEFAULT_STATUSES,
    Attachment,
    Bone,
    Level,
    LogEntry,
    Project,
    Status,
    StatusRole,
    Task,
    TaskState,
    User,
)

from . import db


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RuleError(ValueError):
    """An edit that would break a domain rule (the UI shows the message)."""


Snapshot = dict[str, list[dict[str, Any]]]


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _bone(r: sqlite3.Row) -> Bone:
    return Bone(
        id=r["id"], parent_id=r["parent_id"], position=r["position"], title=r["title"],
        description=r["description"], level_id=r["level_id"],
        level_pinned=bool(r["level_pinned"]), status_id=r["status_id"],
        collapsed=bool(r["collapsed"]), created_at=r["created_at"], updated_at=r["updated_at"],
        created_by=r["created_by"], updated_by=r["updated_by"],
    )


def _level(r: sqlite3.Row) -> Level:
    return Level(id=r["id"], name=r["name"], color=r["color"], definition=r["definition"],
                 sort_order=r["sort_order"], is_default=bool(r["is_default"]),
                 eliminated=bool(r["eliminated"]))


def _status(r: sqlite3.Row) -> Status:
    return Status(id=r["id"], name=r["name"], color=r["color"], role=StatusRole(r["role"]),
                  icon=r["icon"], sort_order=r["sort_order"], is_default=bool(r["is_default"]))


def _task_state(r: sqlite3.Row) -> TaskState:
    return TaskState(id=r["id"], name=r["name"], color=r["color"], symbol=r["symbol"],
                     role=StatusRole(r["role"]), sort_order=r["sort_order"],
                     is_default=bool(r["is_default"]))


def _task(r: sqlite3.Row, bone_ids: list[int]) -> Task:
    return Task(id=r["id"], title=r["title"], state_id=r["state_id"], notes=r["notes"],
                assignee=r["assignee"], due_date=r["due_date"], critical=bool(r["critical"]),
                bone_ids=bone_ids, created_at=r["created_at"], updated_at=r["updated_at"],
                created_by=r["created_by"], updated_by=r["updated_by"])


def _entry(r: sqlite3.Row) -> LogEntry:
    return LogEntry(id=r["id"], bone_id=r["bone_id"], position=r["position"],
                    body_md=r["body_md"], title=r["title"], created_at=r["created_at"],
                    updated_at=r["updated_at"], created_by=r["created_by"],
                    updated_by=r["updated_by"])


class Repository:
    BONE_FIELDS = {"title", "description", "level_id", "level_pinned", "status_id", "collapsed"}
    PROJECT_FIELDS = {"name", "problem_statement", "style_preset", "level_rollup",
                      "show_legend", "legend_position", "show_status"}
    LEGEND_POSITIONS = ("bottom-left", "bottom-right", "right")
    LEVEL_FIELDS = {"name", "color", "definition", "eliminated"}
    STATUS_FIELDS = {"name", "color", "role", "icon"}
    TASK_STATE_FIELDS = {"name", "color", "symbol", "role"}
    TASK_FIELDS = {"title", "notes", "state_id", "assignee", "due_date", "critical"}

    def __init__(self, conn: sqlite3.Connection, path: Path | None = None) -> None:
        self.conn = conn
        self.path = path
        self.user_id: int | None = None
        self._tx_depth = 0

    # ===================================================================== #
    # Opening / creating
    # ===================================================================== #

    @classmethod
    def create(cls, path: str | Path, name: str, problem: str = "", template: str = "6M",
               display_name: str | None = None, levels: list[dict] | None = None,
               statuses: list[dict] | None = None,
               style_preset: str = "Default") -> Repository:
        """Create a project file. `levels` / `statuses` override the built-in defaults
        (FR-3.6 templates); each is a list of dicts like `DEFAULT_LEVELS`."""
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        repo = cls(db.connect(path), path)
        db.migrate(repo.conn, path)
        repo.set_user(display_name)
        with repo.tx():
            ts, uid = now_utc(), repo.user_id
            repo.conn.execute(
                "INSERT INTO project (id, name, problem_statement, style_preset, created_at, "
                "updated_at, created_by, updated_by) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (name, problem, style_preset, ts, ts, uid, uid))
            for i, lv in enumerate(levels or DEFAULT_LEVELS):
                repo.conn.execute(
                    "INSERT INTO level (name, color, definition, sort_order, is_default, "
                    "eliminated, created_at, updated_at, created_by, updated_by) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (lv["name"], lv["color"], lv.get("definition", ""), i,
                     int(lv.get("is_default", False)), int(lv.get("eliminated", False)),
                     ts, ts, uid, uid))
            for i, st in enumerate(statuses or DEFAULT_STATUSES):
                repo.conn.execute(
                    "INSERT INTO status (name, color, role, sort_order, is_default, "
                    "created_at, updated_at, created_by, updated_by) VALUES (?,?,?,?,?,?,?,?,?)",
                    (st["name"], st["color"], StatusRole(st["role"]).value, i,
                     int(st.get("is_default", False)), ts, ts, uid, uid))
            for title in CATEGORY_TEMPLATES[template]:
                repo.add_bone(None, title)
            repo.conn.execute("INSERT INTO meta (key, value) VALUES ('created_with', ?)",
                              (_app_version(),))
            repo._history("create", "project", 1, None, {"name": name, "template": template})
        return repo

    @classmethod
    def open(cls, path: str | Path, display_name: str | None = None) -> Repository:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        conn = db.connect(path)
        try:
            db.check_is_project(conn)
            db.migrate(conn, path)
        except Exception:
            conn.close()
            raise
        repo = cls(conn, path)
        repo.set_user(display_name)
        repo.discard_unowned_attachments()  # images pasted into an editor that never saved
        return repo

    def close(self) -> None:
        self.conn.close()

    def save_copy(self, dest: str | Path) -> None:
        db.backup_to(self.conn, dest)

    # ===================================================================== #
    # Transactions, stamps, history
    # ===================================================================== #

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction; nested `tx()` blocks join the outer one."""
        if self._tx_depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        self._tx_depth += 1
        try:
            yield self.conn
        except BaseException:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self.conn.execute("ROLLBACK")
            raise
        self._tx_depth -= 1
        if self._tx_depth == 0:
            self.conn.execute("COMMIT")

    def _history(self, action: str, entity: str, entity_id: int | None,
                 before: Any, after: Any) -> None:
        def dump(v: Any) -> str | None:
            return None if v is None else json.dumps(v, default=str, ensure_ascii=False)

        self.conn.execute(
            "INSERT INTO history (at, user_id, action, entity, entity_id, before_json, "
            "after_json) VALUES (?,?,?,?,?,?,?)",
            (now_utc(), self.user_id, action, entity, entity_id, dump(before), dump(after)))

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ===================================================================== #
    # Users (FR-6.4 – 6.6)
    # ===================================================================== #

    def set_user(self, display_name: str | None = None) -> int:
        """Make `display_name` (default: the OS account name) the current user."""
        account = _account_name()
        display = (display_name or account or "Unknown").strip()
        row = self.conn.execute(
            "SELECT id FROM user WHERE display_name = ? AND account_name IS ?",
            (display, account)).fetchone()
        if row:
            self.user_id = row["id"]
        else:
            with self.tx():
                cur = self.conn.execute(
                    "INSERT INTO user (display_name, account_name, created_at) VALUES (?,?,?)",
                    (display, account, now_utc()))
            self.user_id = cur.lastrowid
        return self.user_id

    def users(self) -> dict[int, User]:
        return {r["id"]: User(r["id"], r["display_name"], r["account_name"])
                for r in self.conn.execute("SELECT * FROM user")}

    # ===================================================================== #
    # Project
    # ===================================================================== #

    def project(self) -> Project:
        r = self.conn.execute("SELECT * FROM project WHERE id = 1").fetchone()
        return Project(name=r["name"], problem_statement=r["problem_statement"],
                       style_preset=r["style_preset"], level_rollup=bool(r["level_rollup"]),
                       show_legend=bool(r["show_legend"]),
                       legend_position=r["legend_position"],
                       show_status=bool(r["show_status"]),
                       created_at=r["created_at"], updated_at=r["updated_at"])

    def update_project(self, **fields: Any) -> dict[str, Any]:
        """Returns the previous values of the changed fields."""
        self._check_fields(fields, self.PROJECT_FIELDS)
        if fields.get("legend_position", self.LEGEND_POSITIONS[0]) not in self.LEGEND_POSITIONS:
            raise ValueError(f"Unknown legend position: {fields['legend_position']!r}")
        return self._update_logged("project", 1, fields)

    # ===================================================================== #
    # Levels and statuses (FR-3.1 – 3.4, FR-3a.1 – 3a.3)
    # ===================================================================== #

    def levels(self) -> list[Level]:
        return [_level(r) for r in self.conn.execute("SELECT * FROM level ORDER BY sort_order")]

    def statuses(self) -> list[Status]:
        return [_status(r) for r in self.conn.execute("SELECT * FROM status ORDER BY sort_order")]

    def level_template(self) -> list[dict]:
        """The current levels as a template for new projects (FR-3.6)."""
        return [dict(name=lv.name, color=lv.color, definition=lv.definition,
                     is_default=lv.is_default, eliminated=lv.eliminated) for lv in self.levels()]

    def default_level_id(self) -> int:
        r = self.conn.execute("SELECT id FROM level ORDER BY is_default DESC, sort_order DESC "
                              "LIMIT 1").fetchone()
        return r["id"]

    def default_status_id(self) -> int:
        r = self.conn.execute("SELECT id FROM status ORDER BY is_default DESC, sort_order "
                              "LIMIT 1").fetchone()
        return r["id"]

    def add_level(self, name: str, color: str, definition: str = "", eliminated: bool = False,
                  index: int | None = None) -> int:
        return self._add_choice("level", index, dict(
            name=name, color=color, definition=definition, eliminated=int(eliminated)))

    def add_status(self, name: str, color: str, role: StatusRole = StatusRole.ACTIVE,
                   icon: str | None = None, index: int | None = None) -> int:
        return self._add_choice("status", index, dict(
            name=name, color=color, role=StatusRole(role).value, icon=icon))

    def update_level(self, level_id: int, **fields: Any) -> dict[str, Any]:
        self._check_fields(fields, self.LEVEL_FIELDS)
        return self._update_logged("level", level_id, fields)

    def update_status(self, status_id: int, **fields: Any) -> dict[str, Any]:
        self._check_fields(fields, self.STATUS_FIELDS)
        if "role" in fields:
            fields["role"] = StatusRole(fields["role"]).value
        return self._update_logged("status", status_id, fields)

    def move_level(self, level_id: int, new_index: int) -> int:
        return self._move_choice("level", level_id, new_index)

    def move_status(self, status_id: int, new_index: int) -> int:
        return self._move_choice("status", status_id, new_index)

    def set_default_level(self, level_id: int) -> int:
        return self._set_default("level", level_id)

    def set_default_status(self, status_id: int) -> int:
        return self._set_default("status", status_id)

    def delete_level(self, level_id: int, reassign_to: int) -> Snapshot:
        return self._delete_choice("level", "level_id", level_id, reassign_to)

    def delete_status(self, status_id: int, reassign_to: int) -> Snapshot:
        return self._delete_choice("status", "status_id", status_id, reassign_to)

    # ----- task states (configured like statuses; tasks refer to them) ----- #

    def task_states(self) -> list[TaskState]:
        return [_task_state(r) for r in self.conn.execute(
            "SELECT * FROM task_state ORDER BY sort_order")]

    def default_task_state_id(self) -> int:
        r = self.conn.execute("SELECT id FROM task_state ORDER BY is_default DESC, sort_order "
                              "LIMIT 1").fetchone()
        return r["id"]

    def add_task_state(self, name: str, color: str, symbol: str,
                       role: StatusRole = StatusRole.ACTIVE, index: int | None = None) -> int:
        return self._add_choice("task_state", index, dict(
            name=name, color=color, symbol=symbol, role=StatusRole(role).value))

    def update_task_state(self, state_id: int, **fields: Any) -> dict[str, Any]:
        self._check_fields(fields, self.TASK_STATE_FIELDS)
        with self.tx():
            if "role" in fields:
                fields["role"] = StatusRole(fields["role"]).value
                if fields["role"] != StatusRole.DONE.value:
                    self._check_done_role_kept(excluding=state_id)
            return self._update_logged("task_state", state_id, fields)

    def move_task_state(self, state_id: int, new_index: int) -> int:
        return self._move_choice("task_state", state_id, new_index)

    def set_default_task_state(self, state_id: int) -> int:
        return self._set_default("task_state", state_id)

    def delete_task_state(self, state_id: int, reassign_to: int) -> Snapshot:
        return self._delete_choice("task_state", "state_id", state_id, reassign_to,
                                   ref_table="task")

    def _check_done_role_kept(self, excluding: int) -> None:
        if not self.conn.execute("SELECT 1 FROM task_state WHERE role = 'done' AND id != ?",
                                 (excluding,)).fetchone():
            raise RuleError("At least one task state must count as done.")

    def restore_choice(self, snap: Snapshot) -> None:
        """Undo `delete_level` / `delete_status` / `delete_task_state`."""
        table, column = snap["meta"][0]["table"], snap["meta"][0]["column"]
        row = snap["row"][0]
        with self.tx():
            self.conn.execute(
                f"UPDATE {table} SET sort_order = sort_order + 1 WHERE sort_order >= ?",
                (row["sort_order"],))
            self._insert_row(table, row)
            ref_table = snap["meta"][0].get("ref_table", "bone")
            ids = [r["id"] for r in snap["refs"]]
            self.conn.executemany(f"UPDATE {ref_table} SET {column} = ? WHERE id = ?",
                                  [(row["id"], i) for i in ids])
            if row["is_default"]:
                self.conn.execute(f"UPDATE {table} SET is_default = (id = ?)", (row["id"],))
            self._history("restore", table, row["id"], None, row)

    def _add_choice(self, table: str, index: int | None, values: dict[str, Any]) -> int:
        with self.tx():
            n = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            index = n if index is None else max(0, min(index, n))
            self.conn.execute(
                f"UPDATE {table} SET sort_order = sort_order + 1 WHERE sort_order >= ?", (index,))
            ts = now_utc()
            values = {**values, "sort_order": index, "created_at": ts, "updated_at": ts,
                      "created_by": self.user_id, "updated_by": self.user_id}
            new_id = self._insert_row(table, values)
            self._history("create", table, new_id, None, values)
        return new_id

    def _move_choice(self, table: str, row_id: int, new_index: int) -> int:
        with self.tx():
            old = self.conn.execute(f"SELECT sort_order FROM {table} WHERE id = ?",
                                    (row_id,)).fetchone()[0]
            n = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            new_index = max(0, min(new_index, n - 1))
            if new_index != old:
                self.conn.execute(
                    f"UPDATE {table} SET sort_order = sort_order - 1 WHERE sort_order > ?", (old,))
                self.conn.execute(
                    f"UPDATE {table} SET sort_order = sort_order + 1 WHERE sort_order >= ? "
                    f"AND id != ?", (new_index, row_id))
                self._update(table, row_id, {"sort_order": new_index})
                self._history("move", table, row_id, {"index": old}, {"index": new_index})
        return old

    def _set_default(self, table: str, row_id: int) -> int:
        with self.tx():
            old = self.conn.execute(f"SELECT id FROM {table} WHERE is_default = 1").fetchone()
            self.conn.execute(f"UPDATE {table} SET is_default = (id = ?)", (row_id,))
            self._history("set_default", table, row_id, {"id": old and old[0]}, {"id": row_id})
        return old[0] if old else row_id

    def _delete_choice(self, table: str, column: str, row_id: int, reassign_to: int,
                       ref_table: str = "bone") -> Snapshot:
        if reassign_to == row_id:
            raise RuleError("Choose a different value to reassign to.")
        with self.tx():
            count = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if count <= 1:
                raise RuleError(f"At least one {table} is required.")
            row = _row(self.conn.execute(f"SELECT * FROM {table} WHERE id = ?",
                                         (row_id,)).fetchone())
            if row is None or not self.conn.execute(
                    f"SELECT 1 FROM {table} WHERE id = ?", (reassign_to,)).fetchone():
                raise RuleError(f"Unknown {table}.")
            if table == "task_state":
                self._check_done_role_kept(excluding=row_id)
            refs = [dict(r) for r in self.conn.execute(
                f"SELECT id FROM {ref_table} WHERE {column} = ?", (row_id,))]
            self.conn.execute(f"UPDATE {ref_table} SET {column} = ? WHERE {column} = ?",
                              (reassign_to, row_id))
            self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            self.conn.execute(
                f"UPDATE {table} SET sort_order = sort_order - 1 WHERE sort_order > ?",
                (row["sort_order"],))
            if row["is_default"]:
                self.conn.execute(f"UPDATE {table} SET is_default = (id = ?)", (reassign_to,))
            self._history("delete", table, row_id, row, {"reassigned_to": reassign_to})
        return {"meta": [{"table": table, "column": column, "ref_table": ref_table}],
                "row": [row], "refs": refs}

    # ===================================================================== #
    # Bones
    # ===================================================================== #

    def bones(self) -> list[Bone]:
        return [_bone(r) for r in self.conn.execute("SELECT * FROM bone ORDER BY parent_id, "
                                                     "position")]

    def bone(self, bone_id: int) -> Bone:
        r = self.conn.execute("SELECT * FROM bone WHERE id = ?", (bone_id,)).fetchone()
        if r is None:
            raise KeyError(bone_id)
        return _bone(r)

    def child_count(self, parent_id: int | None) -> int:
        return self.conn.execute("SELECT count(*) FROM bone WHERE parent_id IS ?",
                                 (parent_id,)).fetchone()[0]

    def add_bone(self, parent_id: int | None, title: str, index: int | None = None,
                 level_id: int | None = None, status_id: int | None = None,
                 description: str = "") -> Bone:
        with self.tx():
            if parent_id is not None:
                self.bone(parent_id)  # raises if missing
                level_id = level_id or self.default_level_id()
                status_id = status_id or self.default_status_id()
            else:
                level_id = status_id = None
            n = self.child_count(parent_id)
            index = n if index is None else max(0, min(index, n))
            self._open_gap(parent_id, index)
            ts = now_utc()
            values = dict(parent_id=parent_id, position=index, title=title,
                          description=description, level_id=level_id, status_id=status_id,
                          created_at=ts, updated_at=ts, created_by=self.user_id,
                          updated_by=self.user_id)
            bone_id = self._insert_row("bone", values)
            self._history("create", "bone", bone_id, None, values)
        return self.bone(bone_id)

    def update_bone(self, bone_id: int, **fields: Any) -> dict[str, Any]:
        """Update simple fields. Returns their previous values (for undo)."""
        self._check_fields(fields, self.BONE_FIELDS)
        bone = self.bone(bone_id)
        if bone.is_category and ({"level_id", "status_id"} & fields.keys()
                                 or fields.get("level_pinned")):
            raise RuleError("Categories take their level and status from their causes.")
        if not bone.is_category and any(
                fields.get(k, 1) is None for k in ("level_id", "status_id")):
            raise RuleError("Causes must have a level and a status.")
        return self._update_logged("bone", bone_id, fields)

    def move_bone(self, bone_id: int, new_parent: int | None, new_index: int) -> dict[str, Any]:
        """Move (reparent and/or reorder) a bone.

        Moving a cause to the top level converts it to a category; moving a
        category under another bone converts it to a cause (FR-1a.2). Returns
        the previous placement and kind-specific fields, for undo via
        `restore_placement`.
        """
        with self.tx():
            bone = self.bone(bone_id)
            if new_parent is not None and (new_parent == bone_id
                                           or new_parent in self.descendant_ids(bone_id)):
                raise RuleError("A bone can't be moved inside itself.")
            if new_parent is not None:
                self.bone(new_parent)
            before = dict(parent_id=bone.parent_id, index=bone.position, level_id=bone.level_id,
                          status_id=bone.status_id, level_pinned=bone.level_pinned)

            becomes_category = new_parent is None and not bone.is_category
            becomes_cause = new_parent is not None and bone.is_category
            if becomes_category and self.log_entry_count(bone_id):
                raise RuleError("Categories can't hold evidence log entries. Move or delete "
                                "this bone's log entries before making it a category.")

            self._close_gap(bone.parent_id, bone.position, exclude=bone_id)
            n = self.child_count(new_parent) - (1 if new_parent == bone.parent_id else 0)
            new_index = max(0, min(new_index, n))
            self._open_gap(new_parent, new_index, exclude=bone_id)
            fields: dict[str, Any] = dict(parent_id=new_parent, position=new_index)
            if becomes_category:
                fields.update(level_id=None, status_id=None, level_pinned=0)
            elif becomes_cause:
                fields.update(level_id=self.default_level_id(),
                              status_id=self.default_status_id())
            self._update("bone", bone_id, fields)
            self._history("move", "bone", bone_id, before, fields)
        return before

    def restore_placement(self, bone_id: int, before: dict[str, Any]) -> None:
        """Undo `move_bone`, including any category/cause conversion."""
        with self.tx():
            self.move_bone(bone_id, before["parent_id"], before["index"])
            self._update("bone", bone_id, dict(level_id=before["level_id"],
                                                status_id=before["status_id"],
                                                level_pinned=int(before["level_pinned"])))

    def descendant_ids(self, bone_id: int) -> list[int]:
        rows = self.conn.execute(
            "WITH RECURSIVE sub(id) AS (SELECT id FROM bone WHERE parent_id = ? "
            "UNION ALL SELECT b.id FROM bone b JOIN sub ON b.parent_id = sub.id) "
            "SELECT id FROM sub", (bone_id,)).fetchall()
        return [r[0] for r in rows]

    def delete_bone(self, bone_id: int) -> Snapshot:
        """Delete a bone with its subtree, log entries and attachments.

        Returns a snapshot that `restore_subtree` puts back exactly.
        """
        with self.tx():
            snap = self.snapshot_subtree(bone_id)
            root = snap["bones"][0]
            self.conn.execute("DELETE FROM bone WHERE id = ?", (bone_id,))
            self._close_gap(root["parent_id"], root["position"])
            self._history("delete", "bone", bone_id, root,
                          {"descendants": len(snap["bones"]) - 1,
                           "log_entries": len(snap["log_entries"])})
        return snap

    def snapshot_subtree(self, bone_id: int) -> Snapshot:
        ids = [bone_id, *self.descendant_ids(bone_id)]  # parents before children
        marks = ",".join("?" * len(ids))
        by_id = {r["id"]: dict(r) for r in self.conn.execute(
            f"SELECT * FROM bone WHERE id IN ({marks})", ids)}
        entries = [dict(r) for r in self.conn.execute(
            f"SELECT * FROM log_entry WHERE bone_id IN ({marks}) ORDER BY id", ids)]
        entry_ids = [e["id"] for e in entries]
        atts: list[dict[str, Any]] = []
        if entry_ids:
            emarks = ",".join("?" * len(entry_ids))
            atts = [dict(r) for r in self.conn.execute(
                f"SELECT * FROM attachment WHERE log_entry_id IN ({emarks}) ORDER BY id",
                entry_ids)]
        return {"bones": [by_id[i] for i in ids], "log_entries": entries, "attachments": atts}

    def restore_subtree(self, snap: Snapshot) -> None:
        with self.tx():
            root = dict(snap["bones"][0])
            parent = root["parent_id"]
            if parent is not None and not self.conn.execute(
                    "SELECT 1 FROM bone WHERE id = ?", (parent,)).fetchone():
                raise RuleError("The parent of this bone no longer exists.")
            # Undo replays in order, so the original slot is normally free;
            # clamp anyway so siblings stay contiguous if called out of order.
            root["position"] = min(root["position"], self.child_count(parent))
            self._open_gap(parent, root["position"])
            for row in [root, *snap["bones"][1:]]:
                self._insert_row("bone", row)
            for row in snap["log_entries"]:
                self._insert_row("log_entry", row)
            for row in snap["attachments"]:
                self._insert_row("attachment", row)
            self._history("restore", "bone", root["id"], None, {"bones": len(snap["bones"])})

    def _open_gap(self, parent_id: int | None, index: int, exclude: int | None = None) -> None:
        self.conn.execute(
            "UPDATE bone SET position = position + 1 WHERE parent_id IS ? AND position >= ? "
            "AND id IS NOT ?", (parent_id, index, exclude))

    def _close_gap(self, parent_id: int | None, index: int, exclude: int | None = None) -> None:
        self.conn.execute(
            "UPDATE bone SET position = position - 1 WHERE parent_id IS ? AND position > ? "
            "AND id IS NOT ?", (parent_id, index, exclude))

    # ===================================================================== #
    # Log entries (FR-5.x) and attachments
    # ===================================================================== #

    def log_entries(self, bone_id: int) -> list[LogEntry]:
        return [_entry(r) for r in self.conn.execute(
            "SELECT * FROM log_entry WHERE bone_id = ? ORDER BY position", (bone_id,))]

    def log_entry(self, entry_id: int) -> LogEntry:
        r = self.conn.execute("SELECT * FROM log_entry WHERE id = ?", (entry_id,)).fetchone()
        if r is None:
            raise KeyError(entry_id)
        return _entry(r)

    def log_entry_count(self, bone_id: int) -> int:
        return self.conn.execute("SELECT count(*) FROM log_entry WHERE bone_id = ?",
                                 (bone_id,)).fetchone()[0]

    def log_entry_counts(self) -> dict[int, int]:
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT bone_id, count(*) FROM log_entry GROUP BY bone_id")}

    def add_log_entry(self, bone_id: int, body_md: str = "", title: str | None = None,
                      index: int | None = None) -> LogEntry:
        with self.tx():
            if self.bone(bone_id).is_category:
                raise RuleError("Categories don't have an evidence log; add entries to a cause.")
            n = self.log_entry_count(bone_id)
            index = n if index is None else max(0, min(index, n))
            self.conn.execute("UPDATE log_entry SET position = position + 1 "
                              "WHERE bone_id = ? AND position >= ?", (bone_id, index))
            ts = now_utc()
            values = dict(bone_id=bone_id, position=index, title=title, body_md=body_md,
                          created_at=ts, updated_at=ts, created_by=self.user_id,
                          updated_by=self.user_id)
            entry_id = self._insert_row("log_entry", values)
            self._history("create", "log_entry", entry_id, None, values)
        return self.log_entry(entry_id)

    def update_log_entry(self, entry_id: int, **fields: Any) -> dict[str, Any]:
        self._check_fields(fields, {"title", "body_md"})
        return self._update_logged("log_entry", entry_id, fields)

    def move_log_entry(self, entry_id: int, new_index: int) -> int:
        with self.tx():
            e = self.log_entry(entry_id)
            n = self.log_entry_count(e.bone_id)
            new_index = max(0, min(new_index, n - 1))
            if new_index != e.position:
                self.conn.execute("UPDATE log_entry SET position = position - 1 "
                                  "WHERE bone_id = ? AND position > ?", (e.bone_id, e.position))
                self.conn.execute("UPDATE log_entry SET position = position + 1 "
                                  "WHERE bone_id = ? AND position >= ? AND id != ?",
                                  (e.bone_id, new_index, entry_id))
                self._update("log_entry", entry_id, {"position": new_index})
                self._history("move", "log_entry", entry_id, {"index": e.position},
                              {"index": new_index})
        return e.position

    def delete_log_entry(self, entry_id: int) -> Snapshot:
        with self.tx():
            row = _row(self.conn.execute("SELECT * FROM log_entry WHERE id = ?",
                                         (entry_id,)).fetchone())
            if row is None:
                raise KeyError(entry_id)
            atts = [dict(r) for r in self.conn.execute(
                "SELECT * FROM attachment WHERE log_entry_id = ?", (entry_id,))]
            self.conn.execute("DELETE FROM log_entry WHERE id = ?", (entry_id,))
            self.conn.execute("UPDATE log_entry SET position = position - 1 "
                              "WHERE bone_id = ? AND position > ?",
                              (row["bone_id"], row["position"]))
            self._history("delete", "log_entry", entry_id, row, None)
        return {"log_entries": [row], "attachments": atts}

    def restore_log_entry(self, snap: Snapshot) -> None:
        with self.tx():
            row = snap["log_entries"][0]
            self.conn.execute("UPDATE log_entry SET position = position + 1 "
                              "WHERE bone_id = ? AND position >= ?",
                              (row["bone_id"], row["position"]))
            self._insert_row("log_entry", row)
            for att in snap["attachments"]:
                self._insert_row("attachment", att)
            self._history("restore", "log_entry", row["id"], None, row)

    def add_attachment(self, entry_id: int | None, data: bytes, mime: str,
                       filename: str | None = None) -> int:
        """Store an image/file inside the project (FR-5.3, FR-7.3).

        `entry_id` may be None while an entry is still being written; the editor
        then adopts the attachment on save (`assign_attachments`) or discards it on
        cancel (`discard_unowned_attachments`). Identical bytes already attached to
        the same entry are reused.
        """
        digest = hashlib.sha256(data).hexdigest()
        with self.tx():
            if entry_id is not None:
                self.log_entry(entry_id)
            existing = self.conn.execute(
                "SELECT id FROM attachment WHERE log_entry_id IS ? AND sha256 = ?",
                (entry_id, digest)).fetchone()
            if existing:
                return existing[0]
            ts = now_utc()
            att_id = self._insert_row("attachment", dict(
                log_entry_id=entry_id, filename=filename, mime=mime, data=data, sha256=digest,
                created_at=ts, updated_at=ts, created_by=self.user_id, updated_by=self.user_id))
            self._history("create", "attachment", att_id, None,
                          {"log_entry_id": entry_id, "filename": filename, "bytes": len(data)})
        return att_id

    def assign_attachments(self, att_ids: list[int], entry_id: int) -> None:
        """Give unowned attachments (added while editing) to their entry."""
        if not att_ids:
            return
        with self.tx():
            self.log_entry(entry_id)
            self.conn.executemany(
                "UPDATE attachment SET log_entry_id = ? WHERE id = ? AND log_entry_id IS NULL",
                [(entry_id, a) for a in att_ids])

    def discard_unowned_attachments(self, att_ids: list[int] | None = None) -> int:
        """Delete attachments that never got an entry (editor cancelled, or a crash
        mid-edit). With `att_ids` None, removes every unowned attachment."""
        with self.tx():
            if att_ids is None:
                cur = self.conn.execute("DELETE FROM attachment WHERE log_entry_id IS NULL")
            else:
                cur = self.conn.executemany(
                    "DELETE FROM attachment WHERE id = ? AND log_entry_id IS NULL",
                    [(a,) for a in att_ids])
        return cur.rowcount

    def attachment(self, att_id: int) -> Attachment:
        r = self.conn.execute("SELECT id, log_entry_id, mime, sha256, filename, created_at "
                              "FROM attachment WHERE id = ?", (att_id,)).fetchone()
        if r is None:
            raise KeyError(att_id)
        return Attachment(r["id"], r["log_entry_id"], r["mime"], r["sha256"], r["filename"],
                          r["created_at"])

    def all_log_entries(self) -> list[LogEntry]:
        """Every entry in the project, oldest first (for the timeline, FR-5.6)."""
        return [_entry(r) for r in self.conn.execute(
            "SELECT * FROM log_entry ORDER BY created_at, id")]

    def attachments(self, entry_id: int) -> list[Attachment]:
        return [Attachment(r["id"], r["log_entry_id"], r["mime"], r["sha256"], r["filename"],
                           r["created_at"])
                for r in self.conn.execute(
                    "SELECT id, log_entry_id, mime, sha256, filename, created_at FROM attachment "
                    "WHERE log_entry_id = ? ORDER BY id", (entry_id,))]

    def attachment_data(self, att_id: int) -> bytes:
        r = self.conn.execute("SELECT data FROM attachment WHERE id = ?", (att_id,)).fetchone()
        if r is None:
            raise KeyError(att_id)
        return bytes(r[0])

    # ===================================================================== #
    # Tasks
    # ===================================================================== #

    def task_links(self) -> dict[int, set[int]]:
        """Bone id → ids of the tasks linked to it."""
        out: dict[int, set[int]] = {}
        for r in self.conn.execute("SELECT task_id, bone_id FROM task_bone"):
            out.setdefault(r["bone_id"], set()).add(r["task_id"])
        return out

    def _bone_ids_by_task(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for r in self.conn.execute("SELECT task_id, bone_id FROM task_bone "
                                   "ORDER BY created_at, rowid"):
            out.setdefault(r["task_id"], []).append(r["bone_id"])
        return out

    def tasks(self) -> list[Task]:
        links = self._bone_ids_by_task()
        return [_task(r, links.get(r["id"], []))
                for r in self.conn.execute("SELECT * FROM task ORDER BY id")]

    def task(self, task_id: int) -> Task:
        r = self.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if r is None:
            raise KeyError(task_id)
        bone_ids = [b[0] for b in self.conn.execute(
            "SELECT bone_id FROM task_bone WHERE task_id = ? ORDER BY created_at, rowid",
            (task_id,))]
        return _task(r, bone_ids)

    def _clean_task_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        out = dict(fields)
        if "title" in out:
            title = (out["title"] or "").strip()
            if not title:
                raise RuleError("A task needs a title.")
            out["title"] = title
        if "assignee" in out:
            out["assignee"] = (out["assignee"] or "").strip() or None
        if "due_date" in out:
            due = out["due_date"] or None
            if due is not None:
                try:
                    due = date.fromisoformat(str(due)).isoformat()
                except ValueError:
                    raise RuleError(f"“{due}” isn't a date (use YYYY-MM-DD).") from None
            out["due_date"] = due
        if "notes" in out:
            out["notes"] = out["notes"] or ""
        if "critical" in out:
            out["critical"] = int(bool(out["critical"]))
        if "state_id" in out and not self.conn.execute(
                "SELECT 1 FROM task_state WHERE id = ?", (out["state_id"],)).fetchone():
            raise RuleError("Unknown task state.")
        return out

    def add_task(self, title: str, bone_ids: Iterable[int] = (), state_id: int | None = None,
                 **fields: Any) -> int:
        self._check_fields(fields, self.TASK_FIELDS - {"title", "state_id"})
        with self.tx():
            values = self._clean_task_fields(
                {"title": title, "state_id": state_id or self.default_task_state_id(),
                 **fields})
            ts = now_utc()
            values.update(created_at=ts, updated_at=ts, created_by=self.user_id,
                          updated_by=self.user_id)
            task_id = self._insert_row("task", values)
            self._history("create", "task", task_id, None, values)
            for bone_id in bone_ids:
                self.link_task(task_id, bone_id)
        return task_id

    def update_task(self, task_id: int, **fields: Any) -> dict[str, Any]:
        """Change task fields; returns the old values (for undo)."""
        self._check_fields(fields, self.TASK_FIELDS)
        with self.tx():
            return self._update_logged("task", task_id, self._clean_task_fields(fields))

    def delete_task(self, task_id: int) -> Snapshot:
        with self.tx():
            row = _row(self.conn.execute("SELECT * FROM task WHERE id = ?",
                                         (task_id,)).fetchone())
            if row is None:
                raise KeyError(task_id)
            links = [dict(r) for r in self.conn.execute(
                "SELECT * FROM task_bone WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,))]
            self.conn.execute("DELETE FROM task WHERE id = ?", (task_id,))
            self._history("delete", "task", task_id, row, {"links": len(links)})
        return {"task": [row], "links": links}

    def restore_task(self, snap: Snapshot) -> None:
        """Undo `delete_task`, with the same id and links (where the bones still exist)."""
        with self.tx():
            row = snap["task"][0]
            self._insert_row("task", row)
            for link in snap["links"]:
                self.restore_link(link)
            self._history("restore", "task", row["id"], None, row)

    def link_task(self, task_id: int, bone_id: int) -> bool:
        """Link a task to a bone. False if it was already linked."""
        with self.tx():
            self.task(task_id)
            self.bone(bone_id)
            if self.conn.execute("SELECT 1 FROM task_bone WHERE task_id = ? AND bone_id = ?",
                                 (task_id, bone_id)).fetchone():
                return False
            values = dict(task_id=task_id, bone_id=bone_id, created_at=now_utc(),
                          created_by=self.user_id)
            self._insert_row("task_bone", values)
            self._history("link", "task_link", task_id, None, {"bone_id": bone_id})
        return True

    def unlink_task(self, task_id: int, bone_id: int) -> dict[str, Any] | None:
        """Remove a link; returns the removed row (for `restore_link`), or None."""
        with self.tx():
            row = _row(self.conn.execute(
                "SELECT * FROM task_bone WHERE task_id = ? AND bone_id = ?",
                (task_id, bone_id)).fetchone())
            if row is None:
                return None
            self.conn.execute("DELETE FROM task_bone WHERE task_id = ? AND bone_id = ?",
                              (task_id, bone_id))
            self._history("unlink", "task_link", task_id, {"bone_id": bone_id}, None)
        return row

    def restore_link(self, row: dict[str, Any]) -> None:
        with self.tx():
            if not self.conn.execute("SELECT 1 FROM bone WHERE id = ?",
                                     (row["bone_id"],)).fetchone():
                return  # the bone is gone; nothing to link to
            self.conn.execute("INSERT OR IGNORE INTO task_bone (task_id, bone_id, created_at, "
                              "created_by) VALUES (?,?,?,?)",
                              (row["task_id"], row["bone_id"], row["created_at"],
                               row["created_by"]))
            self._history("link", "task_link", row["task_id"], None,
                          {"bone_id": row["bone_id"]})

    # ===================================================================== #
    # Low-level helpers
    # ===================================================================== #

    @staticmethod
    def _check_fields(fields: dict[str, Any], allowed: set[str]) -> None:
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unknown or read-only fields: {sorted(bad)}")

    def _insert_row(self, table: str, values: dict[str, Any]) -> int:
        cols = ",".join(values)
        marks = ",".join("?" * len(values))
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                                tuple(values.values()))
        return cur.lastrowid

    def _update(self, table: str, row_id: int, fields: dict[str, Any]) -> None:
        fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}
        fields.update(updated_at=now_utc(), updated_by=self.user_id)
        sets = ",".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))

    def _update_logged(self, table: str, row_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        with self.tx():
            before = _row(self.conn.execute(f"SELECT * FROM {table} WHERE id = ?",
                                            (row_id,)).fetchone())
            if before is None:
                raise KeyError(row_id)
            old = {k: before[k] for k in fields}
            if old != {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}:
                self._update(table, row_id, fields)
                self._history("update", table, row_id, old, fields)
        return old


def _account_name() -> str | None:
    try:
        return getpass.getuser()
    except Exception:  # no login name available (rare; e.g. some service contexts)
        return None


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("fishy")
    except Exception:
        return "dev"
