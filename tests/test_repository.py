"""Storage tests: schema, rules, snapshots, migrations, crash safety."""

from __future__ import annotations

import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from fishy.domain import BoneTree, StatusRole
from fishy.storage import NewerSchemaError, NotAProjectError, Repository, db
from fishy.storage.repository import RuleError


@pytest.fixture
def repo(tmp_path):
    r = Repository.create(tmp_path / "t.fishy", "Test", "Seal failures", "6M",
                          display_name="Tester")
    yield r
    r.close()


def assert_positions_contiguous(repo: Repository) -> None:
    rows = repo.conn.execute("SELECT parent_id, group_concat(position) FROM "
                             "(SELECT parent_id, position FROM bone ORDER BY position) "
                             "GROUP BY parent_id").fetchall()
    for parent, positions in rows:
        got = [int(p) for p in positions.split(",")]
        assert got == list(range(len(got))), f"positions under {parent}: {got}"


def structure(repo: Repository) -> list[tuple]:
    return [tuple(r) for r in repo.conn.execute(
        "SELECT id, parent_id, position, title, level_id, status_id, level_pinned FROM bone "
        "ORDER BY id")]


# --------------------------------------------------------------------------- #


def test_create_seeds_defaults(repo):
    assert [b.title for b in repo.bones()] == ["People", "Machine", "Method", "Material",
                                                "Measurement", "Environment"]
    assert [lv.name for lv in repo.levels()][0] == "Root Cause"
    assert next(lv for lv in repo.levels() if lv.is_default).name == "Potential Contributor"
    assert next(st for st in repo.statuses() if st.is_default).name == "Not Started"
    assert repo.project().problem_statement == "Seal failures"
    assert db.schema_version(repo.conn) == db.latest_version()
    assert repo.users()[repo.user_id].display_name == "Tester"


def test_templates(tmp_path):
    for name, count in (("4S", 4), ("8P", 8), ("Blank", 0)):
        r = Repository.create(tmp_path / f"{name}.fishy", name, template=name)
        assert len(r.bones()) == count
        r.close()


def test_edits_are_on_disk_immediately_and_single_file(repo, tmp_path):
    cat = repo.bones()[0].id
    repo.add_bone(cat, "Written through")
    other = sqlite3.connect(tmp_path / "t.fishy")  # a second, independent reader
    assert other.execute("SELECT count(*) FROM bone WHERE title = 'Written through'"
                         ).fetchone()[0] == 1
    other.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.fishy"]  # no -wal/-journal


def test_stamps_and_history(repo):
    cat = repo.bones()[0].id
    bone = repo.add_bone(cat, "A")
    assert bone.created_by == bone.updated_by == repo.user_id
    assert bone.created_at.endswith("Z")
    repo.set_user("Someone Else")
    repo.update_bone(bone.id, title="B")
    b = repo.bone(bone.id)
    assert b.created_by != b.updated_by
    h = repo.history(2)
    assert h[0]["action"] == "update" and h[0]["user_id"] == b.updated_by
    assert '"A"' in h[0]["before_json"] and '"B"' in h[0]["after_json"]


def test_update_without_change_writes_nothing(repo):
    bone = repo.add_bone(repo.bones()[0].id, "Same")
    n = len(repo.history(1000))
    repo.update_bone(bone.id, title="Same")
    assert len(repo.history(1000)) == n


def test_category_rules(repo):
    cat = repo.bones()[0]
    with pytest.raises(RuleError):
        repo.update_bone(cat.id, level_id=repo.levels()[0].id)
    with pytest.raises(RuleError):
        repo.add_log_entry(cat.id, "nope")
    cause = repo.add_bone(cat.id, "Cause")
    assert cause.level_id == repo.default_level_id()
    assert cause.status_id == repo.default_status_id()
    with pytest.raises(RuleError):
        repo.update_bone(cause.id, level_id=None)


def test_move_converts_between_category_and_cause(repo):
    c1, c2 = (b.id for b in repo.bones()[:2])
    cause = repo.add_bone(c1, "Will become a category")
    repo.move_bone(cause.id, None, 0)
    b = repo.bone(cause.id)
    assert b.is_category and b.level_id is None and b.status_id is None and b.position == 0
    repo.move_bone(c2, cause.id, 0)  # category -> cause
    b2 = repo.bone(c2)
    assert not b2.is_category and b2.level_id == repo.default_level_id()
    assert_positions_contiguous(repo)


def test_cause_with_evidence_cannot_become_category(repo):
    cause = repo.add_bone(repo.bones()[0].id, "Has evidence")
    repo.add_log_entry(cause.id, "proof")
    with pytest.raises(RuleError):
        repo.move_bone(cause.id, None, 0)


def test_cannot_move_into_own_subtree(repo):
    cat = repo.bones()[0].id
    a = repo.add_bone(cat, "a")
    b = repo.add_bone(a.id, "b")
    for target in (a.id, b.id):
        with pytest.raises(RuleError):
            repo.move_bone(a.id, target, 0)


def test_failed_edit_rolls_back(repo):
    before = structure(repo)
    cat = repo.bones()[0].id
    with pytest.raises(RuleError):
        with repo.tx():
            repo.add_bone(cat, "inside failing transaction")
            raise RuleError("boom")
    assert structure(repo) == before


def test_delete_and_restore_is_exact(repo):
    cat = repo.bones()[0].id
    a = repo.add_bone(cat, "a")
    b = repo.add_bone(a.id, "b")
    repo.add_bone(cat, "sibling after a")
    e = repo.add_log_entry(b.id, "evidence", "Title")
    repo.add_attachment(e.id, b"\x89PNG fake", "image/png", "shot.png")

    def dump():
        return {t: [tuple(r) for r in repo.conn.execute(f"SELECT * FROM {t} ORDER BY id")]
                for t in ("bone", "log_entry", "attachment")}

    original = dump()
    snap = repo.delete_bone(a.id)
    assert repo.log_entry_count(b.id) == 0
    assert_positions_contiguous(repo)
    repo.restore_subtree(snap)
    assert dump() == original


def test_level_delete_reassigns_and_restores(repo):
    levels = repo.levels()
    root, potential = levels[0].id, levels[3].id
    cause = repo.add_bone(repo.bones()[0].id, "x", level_id=root)
    snap = repo.delete_level(root, potential)
    assert repo.bone(cause.id).level_id == potential
    assert [lv.sort_order for lv in repo.levels()] == list(range(len(levels) - 1))
    repo.restore_choice(snap)
    assert repo.bone(cause.id).level_id == root
    assert [lv.id for lv in repo.levels()] == [lv.id for lv in levels]


def test_cannot_delete_last_status(repo):
    statuses = repo.statuses()
    for st in statuses[1:]:
        repo.delete_status(st.id, statuses[0].id)
    with pytest.raises(RuleError):
        repo.delete_status(statuses[0].id, statuses[0].id)


def test_status_roles_validated(repo):
    sid = repo.add_status("Parked", "#000000", StatusRole.BLOCKED)
    assert next(s for s in repo.statuses() if s.id == sid).role is StatusRole.BLOCKED
    with pytest.raises(ValueError):
        repo.update_status(sid, role="sleeping")


def test_log_entries_and_attachments(repo):
    cause = repo.add_bone(repo.bones()[0].id, "c")
    e1 = repo.add_log_entry(cause.id, "first")
    e2 = repo.add_log_entry(cause.id, "second")
    e0 = repo.add_log_entry(cause.id, "zeroth", index=0)
    assert [e.id for e in repo.log_entries(cause.id)] == [e0.id, e1.id, e2.id]
    repo.move_log_entry(e0.id, 2)
    assert [e.id for e in repo.log_entries(cause.id)] == [e1.id, e2.id, e0.id]
    a1 = repo.add_attachment(e1.id, b"bytes", "image/png")
    assert repo.add_attachment(e1.id, b"bytes", "image/png") == a1  # deduplicated
    assert repo.attachment_data(a1) == b"bytes"
    snap = repo.delete_log_entry(e1.id)
    assert [e.position for e in repo.log_entries(cause.id)] == [0, 1]
    repo.restore_log_entry(snap)
    assert repo.attachment_data(a1) == b"bytes"
    assert [e.id for e in repo.log_entries(cause.id)] == [e1.id, e2.id, e0.id]


def test_random_operations_keep_invariants(repo):
    rng = random.Random(3)
    snaps = []
    for _ in range(400):
        bones = repo.bones()
        op = rng.random()
        try:
            if op < 0.4 or not bones:
                parent = rng.choice([None, *[b.id for b in bones]])
                repo.add_bone(parent, "n", index=rng.randint(0, 5))
            elif op < 0.7:
                b = rng.choice(bones)
                repo.move_bone(b.id, rng.choice([None, *[x.id for x in bones]]),
                               rng.randint(0, 6))
            elif op < 0.85:
                snaps.append(repo.delete_bone(rng.choice(bones).id))
            elif snaps:
                repo.restore_subtree(snaps.pop())
        except RuleError:
            pass
        assert_positions_contiguous(repo)
    tree = BoneTree.build(repo.bones())
    assert sum(1 for _ in tree.walk()) == len(repo.bones())  # no orphans or cycles


# ----- opening, migrations ------------------------------------------------- #


def test_reopen(repo, tmp_path):
    repo.add_bone(repo.bones()[0].id, "persisted")
    repo.close()
    r2 = Repository.open(tmp_path / "t.fishy", display_name="Tester")
    assert any(b.title == "persisted" for b in r2.bones())
    assert r2.user_id == repo.user_id  # same person matched, not duplicated
    r2.close()


def test_rejects_non_project_sqlite(tmp_path):
    p = tmp_path / "other.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE x (y)")
    c.close()
    with pytest.raises(NotAProjectError):
        Repository.open(p)


def test_rejects_newer_schema(repo, tmp_path):
    repo.conn.execute("PRAGMA user_version = 99")
    repo.close()
    with pytest.raises(NewerSchemaError):
        Repository.open(tmp_path / "t.fishy")


def test_migration_makes_backup(repo, tmp_path, monkeypatch):
    repo.close()
    current = db._migrations()
    latest = current[-1][0]
    future = [*current, (latest + 1, "ALTER TABLE bone ADD COLUMN owner TEXT;")]
    monkeypatch.setattr(db, "_migrations", lambda: future)
    r = Repository.open(tmp_path / "t.fishy")
    assert db.schema_version(r.conn) == latest + 1
    backup = tmp_path / f"t.fishy.bak-v{latest}"
    assert backup.exists()
    assert sqlite3.connect(backup).execute("PRAGMA user_version").fetchone()[0] == latest
    r.close()


def test_v1_files_upgrade_to_v2(tmp_path, monkeypatch):
    """Projects made with milestones 2–3 (schema v1) open and gain display settings."""
    v1_only = [m for m in db._migrations() if m[0] == 1]
    monkeypatch.setattr(db, "_migrations", lambda: v1_only)
    old = Repository.create(tmp_path / "old.fishy", "Old", "Problem")
    old.add_bone(old.bones()[0].id, "kept")
    old.close()
    monkeypatch.undo()
    r = Repository.open(tmp_path / "old.fishy")
    assert db.schema_version(r.conn) == db.latest_version()
    p = r.project()
    assert p.show_legend and p.show_status and p.legend_position == "bottom-left"
    assert any(b.title == "kept" for b in r.bones())
    assert (tmp_path / "old.fishy.bak-v1").exists()
    r.close()


def test_level_template_round_trip(repo, tmp_path):
    repo.update_level(repo.levels()[0].id, name="Primary Cause", color="#000000")
    template = repo.level_template()
    r2 = Repository.create(tmp_path / "new.fishy", "New", levels=template)
    assert r2.levels()[0].name == "Primary Cause" and r2.levels()[0].color == "#000000"
    assert [lv.is_default for lv in r2.levels()] == [lv.is_default for lv in repo.levels()]
    r2.close()


def test_display_settings_validated(repo):
    repo.update_project(legend_position="right", show_status=False)
    assert repo.project().legend_position == "right" and not repo.project().show_status
    with pytest.raises(ValueError):
        repo.update_project(legend_position="upside-down")


def test_save_copy(repo, tmp_path):
    repo.add_bone(repo.bones()[0].id, "copied")
    repo.save_copy(tmp_path / "copy.fishy")
    r2 = Repository.open(tmp_path / "copy.fishy")
    assert any(b.title == "copied" for b in r2.bones())
    r2.close()


def test_killed_mid_transaction_leaves_file_consistent(repo, tmp_path):
    """NFR-3: the process dies inside a write; the file must be intact and unchanged."""
    path = tmp_path / "t.fishy"
    before = structure(repo)
    repo.close()
    script = f"""
import os
from fishy.storage import Repository
r = Repository.open(r"{path}")
cat = r.bones()[0].id
r.conn.execute("BEGIN IMMEDIATE")
for i in range(200):
    r.conn.execute("INSERT INTO bone (parent_id, position, title, level_id, status_id, "
                   "created_at, updated_at) VALUES (?, ?, 'x', 1, 1, 'a', 'a')", (cat, 100 + i))
os._exit(1)  # hard kill: no commit, no cleanup
"""
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", script], cwd=root, check=False)
    r = Repository.open(path)
    assert r.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert structure(r) == before
    r.close()
