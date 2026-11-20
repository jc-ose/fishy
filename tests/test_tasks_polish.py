"""Tasks polish: prominent state symbols, empty state, list sizing."""

from __future__ import annotations

from PySide6.QtCore import Qt

from fishy.markdown_render import TaskInfo, render


def _section(qtbot, doc, settings, bone_id):
    from fishy.views.tasks import BoneTasksSection

    sec = BoneTasksSection(settings)
    qtbot.addWidget(sec)
    sec.set_document(doc)
    sec.show_bone(bone_id)
    sec.show()
    return sec


def test_glyph_icon_is_large_and_colored(qapp):
    from fishy import theme

    icon = theme.glyph_icon("◐", "#1e88e5", 20)
    img = icon.pixmap(20, 20).toImage()
    assert not icon.isNull() and img.width() >= 20
    colors = {img.pixelColor(x, y).name() for x in range(img.width())
              for y in range(img.height()) if img.pixelColor(x, y).alpha() > 200}
    assert "#1e88e5" in colors


def test_section_rows_use_symbol_icons(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    t = doc.add_task("a", [cause])
    sec = _section(qtbot, doc, tmp_settings, cause)
    item = sec.item_for(t)
    assert not item.icon(sec.COL_SYMBOL).isNull()
    assert item.text(sec.COL_SYMBOL) == ""
    assert sec.list.iconSize().height() >= 18


def test_tasks_window_state_column_has_icon(qtbot, doc):
    from fishy.models.task_table_model import TaskCol
    from fishy.views.tasks import TasksWindow

    doc.add_task("a")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    index = w.model.index(0, TaskCol.STATE)
    assert w.model.data(index) == "To do"
    assert not w.model.data(index, Qt.ItemDataRole.DecorationRole).isNull()


def test_entry_task_symbol_is_larger():
    out = render("[[task:14]]", task_label=lambda _: TaskInfo("◐", "#1e88e5", "t", False))
    assert "font-size: x-large" in out and "font-weight: bold" in out


def test_empty_section_says_so_and_stays_visible(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    sec = _section(qtbot, doc, tmp_settings, cause)
    assert sec.isVisible() and sec.empty_label.isVisible()
    assert sec.empty_label.text() == "No tasks linked to this bone yet."
    assert not sec.list.isVisible()
    doc.add_task("a", [cause])
    assert not sec.empty_label.isVisible() and sec.list.isVisible()


def test_empty_category_message(qtbot, doc, tmp_settings):
    sec = _section(qtbot, doc, tmp_settings, doc.tree.roots()[0])
    assert sec.empty_label.text() == "No tasks on this category or its causes yet."


def test_all_done_says_so(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    t = doc.add_task("a", [cause])
    doc.toggle_task(t)
    sec = _section(qtbot, doc, tmp_settings, cause)
    assert sec.empty_label.isVisible()
    assert sec.empty_label.text() == "All tasks are done."


def test_section_is_separated_from_evidence(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    sec = _section(qtbot, doc, tmp_settings, cause)
    assert sec.separator.isVisible()
    assert sec.layout().contentsMargins().bottom() >= 10


def test_list_height_fits_rows(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    doc.add_task("a", [cause])
    sec = _section(qtbot, doc, tmp_settings, cause)
    one = sec.list.height()
    for name in "bcd":
        doc.add_task(name, [cause])
    assert sec.list.height() > one
    assert one < 80  # no tall empty box for a single task
