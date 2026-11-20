"""Qt lifetime bugs found on Windows (each test reproduces one before its fix)."""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit


def test_hint_reset_timer_dies_with_editor(qtbot, doc_with_cause, monkeypatch):
    """The "No checkbox lines here" reset must not fire into a deleted editor."""
    from fishy.views.evidence import EntryEditor

    doc, cause = doc_with_cause
    monkeypatch.setattr(EntryEditor, "HINT_RESET_MS", 30)
    ed = EntryEditor(doc, cause)
    ed.source.setPlainText("no boxes")
    assert ed.convert_to_task() == 0
    ed.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(ed)
    errors = []
    monkeypatch.setattr("sys.excepthook", lambda *a: errors.append(a))
    qtbot.wait(120)  # past the reset delay
    assert errors == []


def _edit_cell(qtbot, w, row, col):
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    index = w.proxy.index(row, col)
    w.table.setCurrentIndex(index)
    w.table.edit(index)
    QApplication.processEvents()
    editor = w.table.findChild(QLineEdit) if col != 4 else QApplication.focusWidget()
    return QApplication.focusWidget() or editor


def test_inline_edit_commit_keeps_editor_valid(qtbot, qtlog, doc):
    """Committing a cell edit must not reset the model under the open editor."""
    from fishy.models.task_table_model import TaskCol
    from fishy.views.tasks import TasksWindow

    t = doc.add_task("old")
    other = doc.add_task("other")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    for text in ("first", "second"):  # the bug showed on the second edit in a window
        editor = _edit_cell(qtbot, w, 0, TaskCol.TITLE)
        editor.selectAll()
        QTest.keyClicks(editor, text)
        w.table.setCurrentIndex(w.proxy.index(1, TaskCol.ID))  # click another row: commits
        QApplication.processEvents()
        assert doc.tasks[t].title == text
    assert not [r for r in qtlog.records if "does not belong" in r.message]
    assert w.current_task() == other


def test_assignee_enter_commit_keeps_editor_valid(qtbot, qtlog, doc):
    from fishy.models.task_table_model import TaskCol
    from fishy.views.tasks import TasksWindow

    t = doc.add_task("t", assignee="A. Lee")
    doc.add_task("u")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    for name in ("B. Kim", "C. Ode"):
        editor = _edit_cell(qtbot, w, 0, TaskCol.ASSIGNEE)
        editor.setText(name) if isinstance(editor, QLineEdit) else editor.setEditText(name)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QApplication.processEvents()
        assert doc.tasks[t].assignee == name
    assert not [r for r in qtlog.records if "does not belong" in r.message]
    assert w.current_task() == t


def test_edit_keeps_rows_without_reset(qtbot, doc):
    """A field edit updates cells in place; only adding/removing tasks resets."""
    from fishy.views.tasks import TasksWindow

    t = doc.add_task("t")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    resets = []
    w.model.modelReset.connect(lambda: resets.append(1))
    doc.update_task(t, title="renamed")
    assert resets == []
    doc.add_task("new")
    assert resets
