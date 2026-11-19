"""Document tests: undo/redo, write-through, tree model correctness, roll-up."""

from __future__ import annotations

import random

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester

from fishy.document import ProjectDocument
from fishy.domain import BoneTree
from fishy.models.bone_tree_model import BoneIdRole, Col
from fishy.storage import Repository


@pytest.fixture
def doc(qapp, tmp_path):
    d = ProjectDocument.create(tmp_path / "d.fishy", "Doc", "Problem", "6M",
                               display_name="Tester")
    tester = QAbstractItemModelTester(  # noqa: F841  (lives as long as the test)
        d.model, QAbstractItemModelTester.FailureReportingMode.Warning)
    d._tester = tester
    yield d
    d.close()


def struct(doc: ProjectDocument) -> list[tuple]:
    return [tuple(r) for r in doc.repo.conn.execute(
        "SELECT id, parent_id, position, title, level_id, status_id, level_pinned FROM bone "
        "ORDER BY id")]


def model_tree(model, parent=QModelIndex()) -> list:
    out = []
    for row in range(model.rowCount(parent)):
        idx = model.index(row, 0, parent)
        out.append((idx.data(BoneIdRole), model_tree(model, idx)))
    return out


def db_tree(doc: ProjectDocument) -> list:
    tree = BoneTree.build(doc.repo.bones())

    def walk(pid):
        return [(k, walk(k)) for k in tree.kids(pid)]

    return walk(None)


def assert_in_sync(doc: ProjectDocument, qtlog=None) -> None:
    assert model_tree(doc.model) == db_tree(doc)
    if qtlog is not None:
        fails = [r.message for r in qtlog.records if "FAIL" in r.message]
        assert not fails, fails


# --------------------------------------------------------------------------- #


def test_add_rename_undo_redo(doc):
    cat = doc.tree.roots()[0]
    bid = doc.add_bone(cat, "First")
    assert doc.bone(bid).title == "First"
    assert doc.rename_bone(bid, "Renamed")
    assert doc.undo_stack.undoText() == "Rename"
    doc.undo_stack.undo()
    assert doc.bone(bid).title == "First"
    doc.undo_stack.undo()
    assert bid not in doc.tree.bones
    doc.undo_stack.redo()  # same id comes back
    assert doc.bone(bid).title == "First"
    doc.undo_stack.redo()
    assert doc.repo.bone(bid).title == "Renamed"  # undo/redo also written through


def test_rule_violation_emits_error_and_pushes_nothing(doc, qtbot):
    cat = doc.tree.roots()[0]
    count = doc.undo_stack.count()
    with qtbot.waitSignal(doc.error) as sig:
        assert not doc.set_level(cat, next(iter(doc.levels)))
    assert "Categories" in sig.args[0]
    assert doc.undo_stack.count() == count


def test_indent_outdent_and_moves(doc, qtlog):
    cat = doc.tree.roots()[0]
    a = doc.add_bone(cat, "a")
    b = doc.add_bone(cat, "b")
    assert doc.indent(b) and doc.bone(b).parent_id == a
    assert doc.outdent(b) and doc.bone(b).parent_id == cat and doc.bone(b).position == 1
    assert doc.move_up(b) and doc.numbers[b] == "1.1"
    assert not doc.move_up(b)  # already first: no-op, nothing pushed
    assert doc.outdent(b) and doc.bone(b).is_category  # becomes a category (FR-1a.2)
    assert doc.numbers[b] == "2"
    assert_in_sync(doc, qtlog)
    while doc.undo_stack.canUndo():
        doc.undo_stack.undo()
        assert_in_sync(doc, qtlog)


def test_random_edits_undo_to_start_and_redo_to_end(doc, qtlog):
    """Fuzz: every operation must be exactly reversible and replayable."""
    rng = random.Random(11)
    levels, statuses = list(doc.levels), list(doc.statuses)
    start = struct(doc)
    for _ in range(250):
        ids = list(doc.tree.bones)
        op = rng.randrange(9)
        bid = rng.choice(ids) if ids else None
        if op == 0 or bid is None:
            doc.add_bone(rng.choice([None, *ids]), f"n{rng.randint(0, 99)}",
                         rng.randint(0, 4))
        elif op == 1:
            doc.delete_bone(bid)
        elif op == 2:
            doc.move_bone(bid, rng.choice([None, *ids]), rng.randint(0, 5))
        elif op == 3:
            doc.indent(bid)
        elif op == 4:
            doc.outdent(bid)
        elif op == 5:
            doc.rename_bone(bid, f"r{rng.randint(0, 999)}")
        elif op == 6:
            doc.set_level(bid, rng.choice(levels))
        elif op == 7:
            doc.set_status(bid, rng.choice(statuses))
        else:
            doc.set_pinned(bid, rng.random() < 0.5)
        assert_in_sync(doc)
    end = struct(doc)
    while doc.undo_stack.canUndo():
        doc.undo_stack.undo()
    assert struct(doc) == start
    assert_in_sync(doc, qtlog)
    while doc.undo_stack.canRedo():
        doc.undo_stack.redo()
    assert struct(doc) == end
    assert_in_sync(doc, qtlog)


def test_rollup_pin_and_toggle(doc):
    lv = {lv.name: lv.id for lv in doc.levels.values()}
    cat = doc.tree.roots()[0]
    parent = doc.add_bone(cat, "parent")
    c1 = doc.add_bone(parent, "c1")
    c2 = doc.add_bone(parent, "c2")
    doc.set_level(c1, lv["Direct Cause"])
    doc.set_level(c2, lv["Ruled Out"])
    assert doc.effective[parent] == lv["Direct Cause"]  # most severe child
    assert doc.effective[cat] == lv["Direct Cause"]  # categories always roll up
    assert doc.level_is_derived(parent)

    doc.set_level(parent, lv["Root Cause"])  # editing a rolled-up level pins it
    assert doc.bone(parent).level_pinned and doc.effective[parent] == lv["Root Cause"]
    assert doc.effective[cat] == lv["Root Cause"]
    doc.set_pinned(parent, False)
    assert doc.effective[parent] == lv["Direct Cause"]

    doc.set_project_fields(level_rollup=False)  # FR-3.7: configurable
    assert doc.effective[parent] == lv["Root Cause"]  # its own stored level
    doc.undo_stack.undo()
    assert doc.effective[parent] == lv["Direct Cause"]


def test_model_display_and_edit(doc):
    cat = doc.tree.roots()[0]
    bid = doc.add_bone(cat, "cause")
    idx = doc.model.index_for(bid, Col.TITLE)
    assert idx.data() == "cause"
    assert doc.model.setData(idx, "edited in grid")
    assert doc.bone(bid).title == "edited in grid"
    assert doc.model.index_for(bid, Col.NUMBER).data() == "1.1"
    assert doc.model.index_for(cat, Col.STATUS).data() == "0/1 complete"
    assert doc.model.index_for(cat, Col.LEVEL).data().endswith("(auto)")
    assert not doc.model.flags(doc.model.index_for(cat, Col.STATUS)) & Qt.ItemFlag.ItemIsEditable
    done = next(s.id for s in doc.statuses.values() if s.name == "Complete")
    assert doc.model.setData(doc.model.index_for(bid, Col.STATUS), done)
    assert doc.model.index_for(cat, Col.STATUS).data() == "1/1 complete"


def test_levels_and_statuses_are_undoable(doc):
    n = len(doc.levels)
    doc.add_level("Contributing Factor", "#123456", index=2)
    assert len(doc.levels) == n + 1
    new_id = max(doc.levels)
    cause = doc.add_bone(doc.tree.roots()[0], "x", level_id=new_id)
    fallback = doc.ordered_levels()[0].id
    doc.delete_level(new_id, fallback)
    assert doc.bone(cause).level_id == fallback
    doc.undo_stack.undo()  # restore level and its bones
    assert doc.bone(cause).level_id == new_id
    doc.undo_stack.undo()
    doc.undo_stack.undo()  # remove the added level
    assert len(doc.levels) == n


def test_log_entries_undo(doc):
    cause = doc.add_bone(doc.tree.roots()[0], "c")
    e = doc.add_log_entry(cause, "Evidence **bold**", "Records")
    doc.update_log_entry(e, body_md="changed")
    assert doc.model.index_for(cause, Col.LOG).data() == 1
    doc.delete_log_entry(e)
    assert doc.log_counts.get(cause, 0) == 0
    doc.undo_stack.undo()
    doc.undo_stack.undo()
    assert doc.repo.log_entry(e).body_md == "Evidence **bold**"


def test_changes_survive_reopen_without_saving(doc, tmp_path):
    bid = doc.add_bone(doc.tree.roots()[0], "no save button")
    path = doc.path
    doc.close()
    reopened = ProjectDocument(Repository.open(path))
    assert reopened.bone(bid).title == "no save button"
    reopened.close()


def test_layout_nodes_feed_the_diagram(doc):
    lv = {lv.name: lv for lv in doc.levels.values()}
    cat = doc.tree.roots()[0]
    a = doc.add_bone(cat, "a")
    doc.set_level(a, lv["Ruled Out"].id)
    nodes = doc.layout_nodes()
    assert [n.number for n in nodes] == ["1", "2", "3", "4", "5", "6"]
    leaf = nodes[0].children[0]
    assert leaf.dimmed and leaf.color == lv["Ruled Out"].color and leaf.status_color
    assert nodes[0].status_color is None  # categories show a summary, not a dot


def test_changed_signal_fires_for_diagram(doc, qtbot):
    with qtbot.waitSignal(doc.changed):
        doc.add_bone(doc.tree.roots()[0], "x")
    with qtbot.waitSignal(doc.changed):
        doc.undo_stack.undo()
