"""Milestone 7: filtering (UI-7), settings (UI-8), recent projects (FR-7.6),
the already-open warning (FR-7.7)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QModelIndex, QSettings
from PySide6.QtGui import QColor

from fishy import theme
from fishy.app import MainWindow, create_sample_project
from fishy.filtering import BoneFilter, apply_filter
from fishy.project_lock import ProjectLock, lock_path
from fishy.views.choices_dialog import LEVEL_TEMPLATE_KEY
from fishy.views.diagram_view import BoneLineItem, LabelItem
from fishy.views.settings_dialog import (
    NEW_STYLE_KEY,
    NEW_TEMPLATE_KEY,
    USER_NAME_KEY,
    SettingsDialog,
    display_name,
    os_account_name,
)


@pytest.fixture
def doc(qapp, tmp_path):
    d = create_sample_project(tmp_path / "s.fishy")
    yield d
    d.close()


def level_id(doc, name):
    return next(lv.id for lv in doc.levels.values() if lv.name == name)


def status_id(doc, name):
    return next(st.id for st in doc.statuses.values() if st.name == name)


# =========================================================================== #
# Filter rules (pure)
# =========================================================================== #


def test_empty_filter_shows_everything(doc):
    assert apply_filter(doc, BoneFilter()) is None
    assert apply_filter(doc, BoneFilter(text="   ")) is None


def test_level_filter_uses_rolled_up_levels_and_keeps_ancestors(doc):
    root = level_id(doc, "Root Cause")
    r = apply_filter(doc, BoneFilter(level_ids=frozenset({root})))
    assert r.matches == {b for b in doc.tree.walk() if doc.effective.get(b) == root}
    assert r.matches  # the sample has root causes
    for bid in r.matches:
        parent = doc.tree.bones[bid].parent_id
        while parent is not None:
            assert parent in r.visible
            parent = doc.tree.bones[parent].parent_id
    assert not (r.matches & r.context)
    assert r.total == len(doc.tree.bones)


def test_status_filter_never_matches_categories(doc):
    r = apply_filter(doc, BoneFilter(status_ids=frozenset(doc.statuses)))
    cats = set(doc.tree.roots())
    assert not (r.matches & cats)
    assert r.matches == {b for b in doc.tree.walk() if b not in cats}
    assert cats <= r.context


def test_filters_combine_and_across_or_within(doc):
    in_progress, blocked = status_id(doc, "In Progress"), status_id(doc, "Blocked")
    either = apply_filter(doc, BoneFilter(status_ids=frozenset({in_progress, blocked})))
    only = apply_filter(doc, BoneFilter(status_ids=frozenset({in_progress})))
    assert only.matches < either.matches
    pc = level_id(doc, "Potential Contributor")
    both = apply_filter(doc, BoneFilter(level_ids=frozenset({pc}),
                                        status_ids=frozenset({in_progress})))
    assert both.matches == {b for b in only.matches if doc.effective.get(b) == pc}


def test_text_matches_title_description_and_number(doc):
    some = next(b for b in doc.tree.walk() if not doc.tree.bones[b].is_category)
    doc.update_bone(some, description="Look at the XYZZY readings")
    assert apply_filter(doc, BoneFilter(text="xyzzy")).matches == {some}
    title = doc.tree.bones[some].title
    assert some in apply_filter(doc, BoneFilter(text=title.upper()[:6])).matches
    num = doc.numbers[some]
    assert some in apply_filter(doc, BoneFilter(text=num)).matches
    assert apply_filter(doc, BoneFilter(text="no such thing anywhere")).matches == frozenset()


def test_describe(doc):
    f = BoneFilter(text="seal", level_ids=frozenset({level_id(doc, "Root Cause")}))
    assert f.describe(doc) == "“seal”, Root Cause"


# =========================================================================== #
# Window
# =========================================================================== #


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def win(qtbot, tmp_path, settings):
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    return w


def hidden_rows(win) -> set[int]:
    out = set()
    model = win.doc.model

    def walk(parent_id, parent_index):
        for row, bid in enumerate(win.doc.tree.kids(parent_id)):
            if win.tree.isRowHidden(row, parent_index):
                out.add(bid)
            walk(bid, model.index_for(bid))

    walk(None, QModelIndex())
    return out


def test_filter_hides_rows_and_dims_diagram(win):
    doc = win.doc
    root = level_id(doc, "Root Cause")
    win.filter_panel.level_boxes[root].setChecked(True)
    r = win.filter_result
    assert r is not None
    assert hidden_rows(win) == set(doc.tree.bones) - r.visible
    assert win.filter_button.text() == f"Filter: {len(r.matches)} of {r.total}"
    assert win.clear_filter_act.isVisible()
    for item in win.scene.items():
        if isinstance(item, LabelItem) and item.label.bone_id is not None:
            expected = win.scene.FILTER_OPACITY[r.state(item.label.bone_id)]
            assert item.opacity() == pytest.approx(expected)
        if isinstance(item, BoneLineItem) and item.spec.bone_id in r.matches:
            assert item.opacity() == 1.0
    ctx = next(iter(r.context), None)
    if ctx is not None:
        idx = doc.model.index_for(ctx)
        assert idx.data(0x9) == QColor(theme.current().placeholder)  # ForegroundRole

    win.filter_panel.clear()
    assert win.filter_result is None
    assert hidden_rows(win) == set()
    assert all(i.opacity() == 1.0 for i in win.scene.items()
               if isinstance(i, (LabelItem, BoneLineItem)))
    assert win.filter_button.text() == "Filter"


def test_filter_follows_edits(win):
    doc = win.doc
    blocked = status_id(doc, "Blocked")
    win.filter_panel.status_boxes[blocked].setChecked(True)
    before = set(win.filter_result.matches)
    cause = next(b for b in doc.tree.walk()
                 if not doc.tree.bones[b].is_category and b not in before)
    doc.set_status(cause, blocked)
    assert cause in win.filter_result.matches
    doc.undo_stack.undo()
    assert cause not in win.filter_result.matches


def test_current_bone_stays_visible_while_worked_on(win, qtbot):
    doc = win.doc
    blocked = status_id(doc, "Blocked")
    win.filter_panel.status_boxes[blocked].setChecked(True)
    parent = next(iter(win.filter_result.matches))
    new = doc.add_bone(doc.tree.bones[parent].parent_id, "Brand new cause")
    win.select_bone(new)
    win._reapply_filter()
    assert new in win.filter_result.visible
    assert new not in hidden_rows(win)


def test_filter_text_debounced_and_cleared_on_new_project(win, qtbot, tmp_path):
    win.filter_panel.text.setText("seal")
    qtbot.waitUntil(lambda: win.filter_result is not None, timeout=2000)
    assert all("seal" in (win.doc.tree.bones[b].title + win.doc.tree.bones[b].description)
               .lower() or win.doc.numbers[b] == "seal" for b in win.filter_result.matches)
    win._set_doc(create_sample_project(tmp_path / "other.fishy"))
    assert win.filter_result is None and win.filter_panel.text.text() == ""


def test_filter_panel_rebuilds_when_levels_change(win):
    n = len(win.filter_panel.level_boxes)
    win.doc.add_level("Suspected", "#123456")
    assert len(win.filter_panel.level_boxes) == n + 1


def test_exports_ignore_the_filter(win):
    from fishy.export import DiagramExportOptions, build_scene

    win.filter_panel.level_boxes[level_id(win.doc, "Root Cause")].setChecked(True)
    scene = build_scene(win.doc, win.measure, DiagramExportOptions())
    assert all(i.opacity() == 1.0 for i in scene.items() if isinstance(i, LabelItem))


# =========================================================================== #
# Settings (UI-8)
# =========================================================================== #


def test_display_name_defaults_to_account(settings):
    assert display_name(settings) == os_account_name()
    settings.setValue(USER_NAME_KEY, "  Pat Lee ")
    assert display_name(settings) == "Pat Lee"


def test_settings_dialog_saves_and_applies(win, qtbot, settings):
    dlg = SettingsDialog(settings, win)
    qtbot.addWidget(dlg)
    dlg.name.setText("Pat Lee")
    dlg.style.setCurrentText("Compact")
    dlg.template.setCurrentText("4S")
    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    try:
        dlg.accept()
        assert settings.value(USER_NAME_KEY) == "Pat Lee"
        assert settings.value(NEW_STYLE_KEY) == "Compact"
        assert settings.value(NEW_TEMPLATE_KEY) == "4S"
        assert theme.current().dark
    finally:
        theme.manager().set_mode("light")


def test_new_name_stamps_later_edits(win, settings, monkeypatch):
    settings.setValue(USER_NAME_KEY, "Pat Lee")
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: 1)
    win.edit_settings()
    assert "Pat Lee" in win.user_label.text()
    bid = win.doc.add_bone(None, "Tooling")
    assert win.doc.repo.users()[win.doc.tree.bones[bid].created_by].display_name == "Pat Lee"


def test_new_project_uses_defaults(win, settings, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    settings.setValue(NEW_STYLE_KEY, "Compact")
    settings.setValue(NEW_TEMPLATE_KEY, "4S")
    settings.setValue(USER_NAME_KEY, "Pat Lee")
    seen = {}

    def get_item(parent, title, label, items, current, editable):
        seen["default"] = items[current]
        return items[current], True

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Line 4", True))
    monkeypatch.setattr(QInputDialog, "getItem", get_item)
    monkeypatch.setattr(win, "_ask_path", lambda title: tmp_path / "new.fishy")
    win.new_project()
    assert seen["default"] == "4S"
    assert win.doc.project.style_preset == "Compact"
    assert win.doc.project.name == "Line 4"
    assert "Pat Lee" in win.user_label.text()


def test_reset_saved_level_template(win, qtbot, settings):
    settings.setValue(LEVEL_TEMPLATE_KEY, '[{"name": "Mine", "color": "#000000"}]')
    dlg = SettingsDialog(settings, win)
    qtbot.addWidget(dlg)
    assert "Mine" in dlg.levels_label.text() and dlg.reset_levels.isEnabled()
    dlg.reset_levels.click()
    assert not dlg.reset_levels.isEnabled()
    assert settings.value(LEVEL_TEMPLATE_KEY)  # not until OK
    dlg.accept()
    assert settings.value(LEVEL_TEMPLATE_KEY) is None


# =========================================================================== #
# Recent projects (FR-7.6)
# =========================================================================== #


def test_recent_projects_most_recent_first_deduplicated_capped(win, tmp_path):
    paths = []
    for i in range(10):
        p = tmp_path / f"p{i}.fishy"
        create_sample_project(p).close()
        paths.append(p)
        assert win.open_project(p)
    recent = win.recent_projects()
    assert len(recent) == win.RECENT_MAX
    assert recent[0] == str(paths[-1].resolve())
    assert win.open_project(paths[5])
    assert win.recent_projects()[0] == str(paths[5].resolve())
    assert len(set(win.recent_projects())) == len(win.recent_projects())


def test_recent_menu_marks_missing_and_forgets_on_failure(win, tmp_path):
    gone = tmp_path / "gone.fishy"
    create_sample_project(gone).close()
    win.open_project(gone)
    win._set_doc(None)
    gone.unlink()
    win._fill_recent_menu()
    texts = [a.text() for a in win.recent_menu.actions()]
    assert any("(missing)" in t for t in texts)
    win.open_project = MainWindow.open_project.__get__(win)  # real method
    import PySide6.QtWidgets as qw

    orig = qw.QMessageBox.critical
    qw.QMessageBox.critical = staticmethod(lambda *a, **k: None)
    try:
        assert not win.open_project(gone)
    finally:
        qw.QMessageBox.critical = orig
    assert str(gone.resolve()) not in win.recent_projects()


# =========================================================================== #
# Already open elsewhere (FR-7.7)
# =========================================================================== #


@pytest.fixture
def opened(win, tmp_path):
    """A window that opened its project the normal way (so it holds the lock)."""
    path = tmp_path / "locked.fishy"
    create_sample_project(path).close()
    assert win.open_project(path)
    return win


def test_lock_held_while_open_and_released(opened):
    win = opened
    path = win.doc.path
    assert win._lock is not None and win._lock.held
    assert lock_path(path).exists()
    other = ProjectLock(path)
    assert not other.acquire()
    assert other.holder() is not None
    win._set_doc(None)
    assert not lock_path(path).exists()
    assert other.acquire()
    other.release()


def test_second_window_is_warned(opened, qtbot, settings, monkeypatch):
    win = opened
    path = win.doc.path
    second = MainWindow(settings)
    qtbot.addWidget(second)
    asked = []
    monkeypatch.setattr(second, "_ask_open_anyway",
                        lambda name, where: asked.append((name, where)) or False)
    assert not second.open_project(path)
    assert asked and asked[0][0] == path.name and "process" in asked[0][1]
    assert second.doc is None
    monkeypatch.setattr(second, "_ask_open_anyway", lambda name, where: True)
    assert second.open_project(path)
    assert second.doc is not None and second._lock is None  # opened without the lock
    second._set_doc(None)
    assert win._lock.held  # the first window still holds it


def test_reopening_the_open_project_is_a_no_op(win):
    doc = win.doc
    assert win.open_project(doc.path)
    assert win.doc is doc
