"""Tasks: schema v3, task states, task CRUD, links and snapshots."""

from __future__ import annotations

import pytest

from fishy.storage import Repository, db
from fishy.storage.repository import RuleError


@pytest.fixture
def repo(tmp_path):
    r = Repository.create(tmp_path / "t.fishy", "Test", "Seal failures", "6M",
                          display_name="Tester")
    yield r
    r.close()


def test_new_project_has_default_task_states(repo):
    st = repo.task_states()
    assert [(s.name, s.symbol, s.role) for s in st] == [
        ("To do", "☐", "not_started"), ("In progress", "◐", "active"),
        ("Blocked", "⊘", "blocked"), ("Done", "☑", "done")]
    assert repo.default_task_state_id() == st[0].id


def test_v2_file_upgrades_to_v3_with_backup(tmp_path, monkeypatch):
    v2 = [m for m in db._migrations() if m[0] <= 2]
    monkeypatch.setattr(db, "_migrations", lambda: v2)
    Repository.create(tmp_path / "o.fishy", "Old", "P").close()
    monkeypatch.undo()
    r = Repository.open(tmp_path / "o.fishy")
    assert db.schema_version(r.conn) == 3 and len(r.task_states()) == 4
    assert (tmp_path / "o.fishy.bak-v2").exists()
    r.close()


def test_task_crud_links_and_ids_never_reused(repo):
    cause = repo.add_bone(repo.bones()[0].id, "c").id
    t = repo.add_task("Pull records", [cause], assignee="  J. Smith ", due_date="2026-11-24")
    got = repo.task(t)
    assert got.bone_ids == [cause] and got.assignee == "J. Smith" and got.critical is False
    assert got.state_id == repo.default_task_state_id()
    assert repo.link_task(t, cause) is False
    snap = repo.delete_task(t)
    with pytest.raises(KeyError):
        repo.task(t)
    t2 = repo.add_task("x")
    assert t2 > t
    repo.restore_task(snap)
    assert repo.task(t).bone_ids == [cause] and repo.task_links()[cause] == {t}


def test_update_and_unlink_restore(repo):
    cat = repo.bones()[0].id
    t = repo.add_task("t", [cat])
    old = repo.update_task(t, critical=True, assignee="", notes="n")
    assert old == {"critical": 0, "assignee": None, "notes": ""}
    task = repo.task(t)
    assert task.critical is True and task.assignee is None and task.notes == "n"
    row = repo.unlink_task(t, cat)
    assert repo.task(t).bone_ids == [] and repo.unlink_task(t, cat) is None
    repo.restore_link(row)
    assert repo.task(t).bone_ids == [cat]


def test_blank_title_rejected(repo):
    t = repo.add_task("ok")
    with pytest.raises(RuleError, match="A task needs a title."):
        repo.update_task(t, title="   ")
    with pytest.raises(RuleError):
        repo.add_task("")
    assert repo.task(t).title == "ok"


def test_bad_due_date_rejected(repo):
    with pytest.raises(RuleError):
        repo.add_task("t", due_date="24/11/2026")


def test_task_history_rows(repo):
    t = repo.add_task("t", [repo.bones()[0].id])
    repo.update_task(t, title="u")
    entities = {(h["action"], h["entity"]) for h in repo.history(20)}
    assert {("create", "task"), ("update", "task"), ("link", "task_link")} <= entities


def test_last_done_state_protected(repo):
    done = next(s for s in repo.task_states() if s.role == "done")
    todo = repo.default_task_state_id()
    with pytest.raises(RuleError, match="must count as done"):
        repo.delete_task_state(done.id, todo)
    with pytest.raises(RuleError, match="must count as done"):
        repo.update_task_state(done.id, role="active")


def test_task_state_add_move_default(repo):
    sid = repo.add_task_state("Waiting", "#ffa000", "⏸", "blocked", index=1)
    assert [s.name for s in repo.task_states()][:2] == ["To do", "Waiting"]
    repo.move_task_state(sid, 3)
    assert repo.task_states()[3].id == sid
    repo.set_default_task_state(sid)
    assert repo.default_task_state_id() == sid
    repo.update_task_state(sid, symbol="⌛")
    assert next(s for s in repo.task_states() if s.id == sid).symbol == "⌛"


def test_delete_state_reassigns_tasks_and_restores(repo):
    blocked = next(s for s in repo.task_states() if s.role == "blocked")
    t = repo.add_task("t", state_id=blocked.id)
    snap = repo.delete_task_state(blocked.id, repo.default_task_state_id())
    assert repo.task(t).state_id == repo.default_task_state_id()
    repo.restore_choice(snap)
    assert repo.task(t).state_id == blocked.id


def test_status_delete_still_restores(repo):  # regression for the generalised helper
    st = repo.statuses()[1]
    cause = repo.add_bone(repo.bones()[0].id, "c", status_id=st.id).id
    repo.restore_choice(repo.delete_status(st.id, repo.statuses()[0].id))
    assert repo.bone(cause).status_id == st.id
