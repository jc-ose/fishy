"""Milestone 4: levels & statuses editor, legend, diagram markers."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QInputDialog

from fishy.app import MainWindow, create_sample_project
from fishy.domain import StatusRole
from fishy.layout import DEFAULT, LabelKind, approx_measure, layout_fishbone
from fishy.samples import typical
from fishy.views.choices_dialog import ChoicesDialog, saved_level_template

from .test_layout import assert_clean


@pytest.fixture
def win(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.show()
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    return w


@pytest.fixture
def dialog(win, qtbot):
    dlg = ChoicesDialog(win.doc, win.settings, win)
    qtbot.addWidget(dlg)
    return dlg


def level_named(doc, name):
    return next(lv for lv in doc.levels.values() if lv.name == name)


def bone_by_title(doc, prefix):
    return next(b.id for b in doc.tree.bones.values() if b.title.startswith(prefix))


# ----- levels & statuses editor ----------------------------------------------- #


def test_add_rename_recolor_level(dialog, win, monkeypatch):
    tab = dialog.levels_tab
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Systemic", True)))
    tab.list.setCurrentRow(0)
    tab._add()
    assert [lv.name for lv in win.doc.ordered_levels()][1] == "Systemic"
    assert tab.list.item(1).text() == "Systemic"
    tab.list.setCurrentRow(1)
    tab.name.setText("Systemic Cause")
    tab._name_edited()
    tab._update(color="#123456")
    lv = win.doc.ordered_levels()[1]
    assert (lv.name, lv.color) == ("Systemic Cause", "#123456")
    win.doc.undo_stack.undo()
    win.doc.undo_stack.undo()
    win.doc.undo_stack.undo()
    assert "Systemic" not in [lv.name for lv in win.doc.ordered_levels()]


def test_reordering_levels_changes_rollup(dialog, win):
    doc = win.doc
    parent = bone_by_title(doc, "Operator unfamiliar")  # children: Root Cause + Immediate
    assert doc.effective[parent] == level_named(doc, "Root Cause").id
    tab = dialog.levels_tab
    tab.list.setCurrentRow(0)  # Root Cause
    tab._move(+1)
    tab._move(+1)  # now below Immediate Cause: Immediate is the most severe child
    assert doc.effective[parent] == level_named(doc, "Immediate Cause").id


def test_delete_in_use_level_reassigns(dialog, win, monkeypatch):
    doc = win.doc
    root = level_named(doc, "Root Cause")
    users = [b.id for b in doc.tree.bones.values() if b.level_id == root.id]
    assert users
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("Direct Cause", True)))
    tab = dialog.levels_tab
    tab.list.setCurrentRow(0)
    tab._delete()
    assert root.id not in doc.levels
    direct = level_named(doc, "Direct Cause").id
    assert all(doc.bone(b).level_id == direct for b in users)
    doc.undo_stack.undo()
    assert all(doc.bone(b).level_id == root.id for b in users)


def test_default_eliminated_and_roles(dialog, win):
    doc = win.doc
    tab = dialog.levels_tab
    tab.list.setCurrentRow(1)  # Direct Cause
    tab.default_box.setChecked(True)
    assert level_named(doc, "Direct Cause").is_default
    new = doc.add_bone(doc.tree.roots()[0], "uses new default")
    assert doc.levels[doc.bone(new).level_id].name == "Direct Cause"
    tab.eliminated.setChecked(True)
    assert level_named(doc, "Direct Cause").eliminated
    assert next(n for n in doc.layout_nodes()[0].children if n.id == new).dimmed

    stab = dialog.status_tab
    stab.list.setCurrentRow(stab.list.count() - 1)  # Complete
    stab.role.setCurrentIndex(stab.role.findData(StatusRole.REVIEW))
    stab._role_chosen(0)
    assert doc.summary(doc.tree.roots()[0]).done == 0  # "Complete" no longer counts as done


def test_definition_saves_to_the_right_level_when_switching_rows(dialog, win):
    tab = dialog.levels_tab
    tab.list.setCurrentRow(0)
    tab.definition.setPlainText("Edited root definition")
    tab.list.setCurrentRow(2)  # switch rows before the idle timer fires
    assert level_named(win.doc, "Root Cause").definition == "Edited root definition"
    assert level_named(win.doc, "Immediate Cause").definition != "Edited root definition"


def test_dialog_undo_shortcut(dialog, win, qtbot):
    dialog.show()
    tab = dialog.levels_tab
    tab.list.setCurrentRow(0)
    tab._update(color="#000000")
    qtbot.keyClick(dialog, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert level_named(win.doc, "Root Cause").color != "#000000"


def test_level_template_for_new_projects(dialog, win, tmp_path):
    win.doc.update_level(level_named(win.doc, "Root Cause").id, name="Primary")
    dialog.save_template()
    levels = saved_level_template(win.settings)
    assert levels[0]["name"] == "Primary"
    from fishy.document import ProjectDocument

    doc2 = ProjectDocument.create(tmp_path / "n.fishy", "N", levels=levels)
    assert doc2.ordered_levels()[0].name == "Primary"
    doc2.close()


# ----- legend ----------------------------------------------------------------- #


def test_legend_positions_and_toggles(win):
    doc, scene = win.doc, win.scene
    legend = scene.legend_item
    assert legend is not None
    labels_bottom = max(i.sceneBoundingRect().bottom() for i in scene._labels.values())
    assert legend.sceneBoundingRect().top() > labels_bottom  # bottom-left: below diagram
    assert scene.sceneRect().contains(legend.sceneBoundingRect())  # included in exports
    assert legend.status_chips  # status key shown with status dots

    doc.set_project_fields(legend_position="right")
    head_right = max(i.sceneBoundingRect().right() for i in scene.items()
                     if getattr(i, "label", None) and i.label.kind is LabelKind.HEAD)
    assert scene.legend_item.sceneBoundingRect().left() > head_right

    doc.set_project_fields(show_status=False)
    assert not scene.legend_item.status_chips
    assert all(i.label.status_dot is None for i in scene._labels.values())

    doc.set_project_fields(show_legend=False)
    assert scene.legend_item is None
    assert not win.legend_pos.isEnabled()
    doc.undo_stack.undo()
    assert scene.legend_item is not None


def test_exports_hide_selection(win, tmp_path):
    view = win.view
    view.export_png(str(tmp_path / "plain.png"), 1.0)
    bone = bone_by_title(win.doc, "HVAC")
    win.select_bone(bone)
    view.export_png(str(tmp_path / "selected.png"), 1.0)
    assert QImage(str(tmp_path / "plain.png")) == QImage(str(tmp_path / "selected.png"))
    assert win.scene.selected_bone() == bone  # selection restored after export


# ----- markers ---------------------------------------------------------------- #


def test_pin_and_progress_markers_reserve_space():
    problem, cats = typical()
    pinned = cats[0].children[0]
    pinned.pinned = True
    cats[0].progress = (3, 5)
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    lab = d.label_for(pinned.id)
    assert lab.pin_mark is not None and lab.pin_mark.right <= lab.text_rect.x
    cat = d.label_for(cats[0].id)
    assert cat.progress == (3, 5) and cat.progress_rect.right <= cat.text_rect.x
    assert_clean(d)
    hidden = layout_fishbone(problem, cats, replace(DEFAULT, show_status=False), approx_measure)
    assert hidden.label_for(cats[0].id).progress_rect is None


def test_document_marks_only_meaningful_pins(win):
    doc = win.doc
    leaf = bone_by_title(doc, "HVAC")
    doc.set_pinned(leaf, True)  # a leaf has nothing to override
    parent = bone_by_title(doc, "Seal press")
    doc.set_level(parent, level_named(doc, "Root Cause").id)  # pins a parent
    nodes = {n.id: n for cat in doc.layout_nodes() for n in _walk(cat)}
    assert not nodes[leaf].pinned and nodes[parent].pinned
    assert nodes[doc.tree.roots()[0]].progress == (3, 5)


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)
