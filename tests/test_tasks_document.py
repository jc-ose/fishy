"""Tasks: document caches, signals and undoable commands."""

from __future__ import annotations

from datetime import date


def test_add_toggle_undo_redo_keeps_id(doc, qtbot):
    cause = doc.add_bone(doc.tree.roots()[0], "c")
    with qtbot.waitSignal(doc.tasksChanged):
        t = doc.add_task("Pull records", [cause])
    assert doc.toggle_task(t) and doc.is_task_done(t)
    doc.undo_stack.undo()
    assert not doc.is_task_done(t)
    doc.undo_stack.undo()
    assert t not in doc.tasks
    doc.undo_stack.redo()
    assert doc.tasks[t].title == "Pull records"
    assert doc.tasks[t].bone_ids == [cause]


def test_delete_task_undo_restores_links(doc):
    cause = doc.add_bone(doc.tree.roots()[0], "c")
    t = doc.add_task("t", [cause])
    doc.delete_task(t)
    assert cause not in doc.task_links
    doc.undo_stack.undo()
    assert doc.task_links[cause] == {t}


def test_unlink_and_bone_delete_undo(doc):
    cat = doc.tree.roots()[0]
    cause = doc.add_bone(cat, "c")
    t = doc.add_task("t", [cause, cat])
    doc.delete_bone(cause)
    assert doc.tasks[t].bone_ids == [cat]
    doc.undo_stack.undo()
    assert sorted(doc.tasks[t].bone_ids) == sorted([cat, cause])
    doc.unlink_task(t, cat)
    doc.undo_stack.undo()
    assert cat in doc.tasks[t].bone_ids


def test_link_set_state_cycle_update(doc):
    cat = doc.tree.roots()[0]
    t = doc.add_task("t")
    assert doc.link_task(t, cat) and doc.tasks[t].bone_ids == [cat]
    states = doc.ordered_task_states()
    assert doc.set_task_state(t, states[2].id) and doc.tasks[t].state_id == states[2].id
    assert doc.cycle_task(t) and doc.tasks[t].state_id == states[3].id
    assert doc.update_task(t, assignee=" A. Lee ", due_date="2026-11-01")
    assert doc.assignees() == ["A. Lee"]
    doc.today = lambda: date(2026, 11, 19)
    doc.cycle_task(t)  # wraps to "To do" -> open again
    assert doc.is_task_overdue(t)
    doc.undo_stack.undo()
    doc.undo_stack.undo()
    assert doc.tasks[t].assignee is None


def test_category_open_info_rolls_up(doc):
    cat = doc.tree.roots()[0]
    a = doc.add_bone(cat, "a")
    a1 = doc.add_bone(a, "a1")
    doc.add_task("x", [a1], critical=True)
    done = doc.add_task("y", [a1])
    doc.toggle_task(done)
    assert doc.open_task_info(cat) == (1, True)
    assert doc.open_task_info(a) == (0, False)  # parent causes: direct only
    assert doc.open_task_info(a1) == (1, True)


def test_task_groups_sorted_for_display(doc):
    cause = doc.add_bone(doc.tree.roots()[0], "c")
    late = doc.add_task("late", [cause], due_date="2026-12-01")
    soon = doc.add_task("soon", [cause], due_date="2026-11-01")
    assert doc.task_groups(cause) == [(cause, [soon, late])]


def test_task_state_commands_undo(doc, qtbot):
    with qtbot.waitSignal(doc.tasksChanged):
        assert doc.add_task_state("Waiting", "#ffa000", "⏸")
    sid = doc.ordered_task_states()[-1].id
    assert doc.update_task_state(sid, symbol="⌛") and doc.task_states[sid].symbol == "⌛"
    t = doc.add_task("t", state_id=sid)
    assert doc.delete_task_state(sid, doc.ordered_task_states()[0].id)
    assert doc.tasks[t].state_id == doc.ordered_task_states()[0].id
    doc.undo_stack.undo()
    assert doc.tasks[t].state_id == sid


def test_rejected_edit_emits_error_and_pushes_nothing(doc, qtbot):
    t = doc.add_task("ok")
    n = doc.undo_stack.count()
    with qtbot.waitSignal(doc.error):
        assert doc.update_task(t, title=" ") is False
    assert doc.undo_stack.count() == n
