"""Main window tests (milestone 3): keyboard outline editing, dropdowns, drag-and-drop,
details panel, diagram ↔ editor sync, pane layout."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox

from fishy.app import MainWindow, create_sample_project
from fishy.models.bone_tree_model import Col
from fishy.views.bone_tree_view import ChoiceDelegate


@pytest.fixture
def answer(monkeypatch):
    """Control QMessageBox.question answers; records the questions asked."""
    state = {"reply": QMessageBox.StandardButton.Yes, "asked": []}

    def fake(_parent, _title, text, *a, **k):
        state["asked"].append(text)
        return state["reply"]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake))
    return state


@pytest.fixture
def win(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.show()
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    qtbot.waitExposed(w)
    return w


def bone_by_title(win, prefix: str) -> int:
    return next(b.id for b in win.doc.tree.bones.values() if b.title.startswith(prefix))


def press(qtbot, win, key, mods=Qt.KeyboardModifier.NoModifier):
    win.tree.setFocus()
    qtbot.keyClick(win.tree, key, mods)


# ----- keyboard (user's bindings) --------------------------------------------- #


def test_enter_adds_sibling_and_typing_names_it(win, qtbot):
    a = bone_by_title(win, "Operator unfamiliar")  # 1.1
    win.select_bone(a)
    press(qtbot, win, Qt.Key.Key_Return)
    new = win.tree.current_bone()
    assert new != a and win.doc.numbers[new] == "1.2"
    assert win.tree.state() == win.tree.State.EditingState
    editor = QApplication.focusWidget()
    assert isinstance(editor, QLineEdit)
    editor.selectAll()
    qtbot.keyClicks(editor, "Typed by keyboard")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: win.tree.state() != win.tree.State.EditingState)  # queued commit
    assert win.doc.bone(new).title == "Typed by keyboard"
    press(qtbot, win, Qt.Key.Key_Return)  # Enter again: another sibling
    assert win.doc.numbers[win.tree.current_bone()] == "1.3"


def test_tab_adds_child_space_renames(win, qtbot):
    a = bone_by_title(win, "Shift handover")  # 1.2, a leaf
    win.select_bone(a)
    press(qtbot, win, Qt.Key.Key_Tab)
    child = win.tree.current_bone()
    assert win.doc.bone(child).parent_id == a
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    press(qtbot, win, Qt.Key.Key_Space)
    assert win.tree.state() == win.tree.State.EditingState
    assert win.tree.currentIndex().column() == Col.TITLE


def test_arrows_indent_outdent_and_ctrl_arrows_move(win, qtbot, answer):
    b = bone_by_title(win, "Shift handover")  # 1.2
    win.select_bone(b)
    press(qtbot, win, Qt.Key.Key_Right)  # indent under 1.1
    assert win.doc.numbers[b] == "1.1.3"
    assert win.tree.current_bone() == b  # selection follows the bone
    press(qtbot, win, Qt.Key.Key_Left)  # back out
    assert win.doc.numbers[b] == "1.2"
    press(qtbot, win, Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
    assert win.doc.numbers[b] == "1.1"
    press(qtbot, win, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert win.doc.numbers[b] == "1.2"
    assert not answer["asked"]


def test_outdent_to_category_asks_first(win, qtbot, answer):
    b = bone_by_title(win, "Shift handover")
    win.select_bone(b)
    answer["reply"] = QMessageBox.StandardButton.No
    press(qtbot, win, Qt.Key.Key_Left)
    assert answer["asked"] and not win.doc.bone(b).is_category
    answer["reply"] = QMessageBox.StandardButton.Yes
    press(qtbot, win, Qt.Key.Key_Left)
    assert win.doc.bone(b).is_category


def test_delete_key_confirms_subtrees_and_selects_neighbour(win, qtbot, answer):
    parent = bone_by_title(win, "Operator unfamiliar")  # has 2 children + evidence
    win.select_bone(parent)
    press(qtbot, win, Qt.Key.Key_Delete)
    assert "2 sub-cause(s) and 1 evidence entry" in answer["asked"][0]
    assert parent not in win.doc.tree.bones
    assert win.tree.current_bone() == bone_by_title(win, "Shift handover")
    win.undo_act.trigger()
    assert parent in win.doc.tree.bones


# ----- dropdown editors (note 2) --------------------------------------------- #


def test_level_and_status_dropdowns_show_names(win, qtbot):
    leaf = bone_by_title(win, "Shift handover")
    model = win.doc.model
    delegate = win.tree.itemDelegateForColumn(Col.LEVEL)
    assert isinstance(delegate, ChoiceDelegate)
    idx = model.index_for(leaf, Col.LEVEL)
    combo = delegate.createEditor(win.tree.viewport(), None, idx)
    assert isinstance(combo, QComboBox)
    names = [combo.itemText(i) for i in range(combo.count())]
    assert names[0] == "Root Cause" and "Ruled Out" in names
    delegate.setEditorData(combo, idx)
    assert combo.currentText() == "Potential Contributor"
    combo.setCurrentIndex(names.index("Direct Cause"))
    delegate.setModelData(combo, model, idx)
    assert win.doc.levels[win.doc.bone(leaf).level_id].name == "Direct Cause"

    sdel = win.tree.itemDelegateForColumn(Col.STATUS)
    sidx = model.index_for(leaf, Col.STATUS)
    scombo = sdel.createEditor(win.tree.viewport(), None, sidx)
    scombo.setCurrentIndex(scombo.findText("Blocked"))
    sdel.setModelData(scombo, model, sidx)
    assert win.doc.statuses[win.doc.bone(leaf).status_id].name == "Blocked"


# ----- drag and drop ---------------------------------------------------------- #


def test_drag_and_drop_moves_and_confirms_conversion(win, answer):
    model = win.doc.model
    leaf = bone_by_title(win, "Worn die")  # 2.2
    target = bone_by_title(win, "Method")  # category 3
    data = model.mimeData([model.index_for(leaf)])
    model.dropMimeData(data, Qt.DropAction.MoveAction, -1, 0, model.index_for(target))
    assert win.doc.bone(leaf).parent_id == target  # dropped onto: last child
    assert win.doc.numbers[leaf] == "3.3"

    # Reorder within the same parent (drop above row 0).
    model.dropMimeData(model.mimeData([model.index_for(leaf)]), Qt.DropAction.MoveAction,
                       0, 0, model.index_for(target))
    assert win.doc.numbers[leaf] == "3.1"

    # Can't drop a bone into its own subtree.
    parent = bone_by_title(win, "Operator unfamiliar")
    child = win.doc.tree.kids(parent)[0]
    assert not model.canDropMimeData(model.mimeData([model.index_for(parent)]),
                                     Qt.DropAction.MoveAction, -1, 0, model.index_for(child))

    # Dropping on the empty area makes a category: asks first.
    answer["reply"] = QMessageBox.StandardButton.No
    from PySide6.QtCore import QModelIndex
    model.dropMimeData(model.mimeData([model.index_for(leaf)]), Qt.DropAction.MoveAction,
                       -1, 0, QModelIndex())
    assert answer["asked"] and not win.doc.bone(leaf).is_category


# ----- details panel ---------------------------------------------------------- #


def test_details_panel_edits(win, qtbot):
    parent = bone_by_title(win, "Seal press")  # 2.1, has children
    win.select_bone(parent)
    d = win.details
    assert d.bone_id == parent and "2.1" in d.heading.text()
    assert "Rolled up" in d.level_hint.text() and d.pin.isVisible()

    d.title.setText("Seal press temperature drift (confirmed)")
    d.title.editingFinished.emit()
    assert win.doc.bone(parent).title.endswith("(confirmed)")

    d.description.setPlainText("Logged drift of +6 °C over 4 h.")
    d._commit_description()
    assert win.doc.bone(parent).description.startswith("Logged drift")

    root = next(lv for lv in win.doc.ordered_levels() if lv.name == "Root Cause")
    d.level.setCurrentIndex(d.level.findData(root.id))
    d.level.activated.emit(d.level.currentIndex())
    assert win.doc.bone(parent).level_pinned  # choosing a rolled-up level pins it
    assert d.pin.isChecked()


def test_details_for_category_and_evidence(win):
    cat = bone_by_title(win, "People")
    win.select_bone(cat)
    d = win.details
    assert d.kind_label.text() == "Category"
    assert "complete" in d.summary.text() and not d.level.isEnabled()
    assert d.evidence.isHidden() and not d.category_note.isHidden()
    win.select_bone(bone_by_title(win, "Training record"))
    assert not d.evidence.isHidden() and d.evidence.heading.text() == "Evidence log (1)"
    assert d.evidence.card_for(win.doc.repo.log_entries(win.details.bone_id)[0].id) \
        .title.text() == "Training records"


def test_details_typing_flushes_when_switching_bones(win):
    a = bone_by_title(win, "Shift handover")
    win.select_bone(a)
    win.details.description.setPlainText("typed but not yet committed")
    win.select_bone(bone_by_title(win, "Worn die"))
    assert win.doc.bone(a).description == "typed but not yet committed"


# ----- diagram ↔ editor ------------------------------------------------------- #


def test_selection_sync_and_double_click(win):
    bone = bone_by_title(win, "HVAC")
    win.scene.select_bone(bone)  # as if clicked in the diagram
    assert win.tree.current_bone() == bone and win.details.bone_id == bone
    other = bone_by_title(win, "Worn die")
    win.select_bone(other)  # as if picked in the tree
    assert win.scene.selected_bone() == other
    win.show_panes("diagram")
    win.scene.boneActivated.emit(bone)  # double-click
    assert win.editor_split.isVisible() and win.details.bone_id == bone


def test_find(win):
    win.find_box.setText("supplier")  # two matches: 4.1 and 4.1.2
    win.find_next(restart=True)
    first = win.tree.current_bone()
    assert win.doc.numbers[first] == "4.1"
    win.find_next()
    assert win.doc.numbers[win.tree.current_bone()] == "4.1.2"
    win.find_next()  # wraps around
    assert win.tree.current_bone() == first


def test_progress_summary(win):
    assert win.progress_label.text().startswith("5/20 causes complete · 2 blocked")


# ----- pane layout (UI-2) ----------------------------------------------------- #


def test_maximize_and_layout_is_remembered(win, qtbot, tmp_path):
    win.show_panes("editor")
    assert not win.view.isVisible() and win.editor_split.isVisible()
    win.toggle_orientation()
    win.close()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w2 = MainWindow(settings)
    qtbot.addWidget(w2)
    assert w2.pane_actions["editor"].isChecked()
    assert w2.main_split.orientation() == Qt.Orientation.Horizontal


def test_diagram_stays_fitted_until_user_zooms(win, qtbot):
    view = win.view
    win.show_panes("diagram")
    qtbot.wait(20)
    full = view.transform().m11()
    win.show_panes("both")
    qtbot.wait(20)
    assert view.transform().m11() < full  # shrank to fit the smaller pane
    view.scale(1.5, 1.5)
    view.auto_fit = False  # as wheelEvent does
    zoomed = view.transform().m11()
    win.doc.add_bone(win.doc.tree.roots()[0], "grows the diagram")
    assert view.transform().m11() == zoomed  # user's zoom is respected
    win.view.fit_all()
    assert view.auto_fit
