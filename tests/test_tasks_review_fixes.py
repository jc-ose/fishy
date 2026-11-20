"""Fixes from the final whole-branch review (each test failed before its fix)."""

from __future__ import annotations

import csv
import io

import shiboken6
from PySide6.QtCore import QCoreApplication, QDate, QEvent, QSettings
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from fishy.task_markup import convert_checkboxes


# I1: a checkbox line with no text must not become a task (its save would fail).
def test_blank_checkbox_line_not_converted():
    assert convert_checkboxes("- [ ]   \n- [ ] real\n", 0, 20) == (
        "- [ ]   \n- [[task:new]] real\n", 1)


# I1: if the save is rejected anyway, the editor comes back with the text.
def test_failed_save_reopens_editor(qtbot, doc_with_cause, tmp_settings, monkeypatch):
    from fishy.views.evidence import EntryEditor, EvidenceLog

    doc, cause = doc_with_cause
    log = EvidenceLog(tmp_settings)
    qtbot.addWidget(log)
    log.set_document(doc)
    log.show_bone(cause)
    shown = []

    def exec_(self):
        shown.append(self.body())
        if not self.body():
            self.source.setPlainText("keep me")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EntryEditor, "exec", exec_)
    real = doc.save_log_entry
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        return None if len(calls) == 1 else real(*a, **k)

    monkeypatch.setattr(doc, "save_log_entry", flaky)
    eid = log.add_entry()
    assert shown == ["", "keep me"] and doc.repo.log_entry(eid).body_md == "keep me"


# Promoted minor: editors are deleted after use (else every preview re-renders forever).
def test_entry_editor_deleted_after_use(qtbot, doc_with_cause, tmp_settings, monkeypatch):
    from fishy.views.evidence import EntryEditor, EvidenceLog

    doc, cause = doc_with_cause
    log = EvidenceLog(tmp_settings)
    qtbot.addWidget(log)
    log.set_document(doc)
    log.show_bone(cause)
    made = []
    monkeypatch.setattr(EntryEditor, "exec",
                        lambda self: made.append(self) or QDialog.DialogCode.Rejected)
    log.add_entry()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert made and not shiboken6.isValid(made[0])


def _window(qtbot, tmp_path):
    from fishy.app import MainWindow, create_sample_project

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    return w


# I2: task editors close (saving pending text) when the project closes.
def test_task_editors_close_with_project(qtbot, tmp_path):
    from fishy.document import ProjectDocument
    from fishy.views.tasks import _open_editors, open_task_editor

    w = _window(qtbot, tmp_path)
    t = w.doc.add_task("t")
    ed = open_task_editor(w.doc, t, w)
    ed.notes.setPlainText("typed but not committed")
    w._set_doc(None)
    assert not [k for k in _open_editors if k[1] == t]
    doc = ProjectDocument.open(tmp_path / "s.fishy")
    assert doc.tasks[t].notes == "typed but not committed"
    doc.close()


# I3: editors opened from the Tasks window navigate in the main window.
def test_editor_from_tasks_window_selects_bone(qtbot, tmp_path):
    w = _window(qtbot, tmp_path)
    bone = list(w.doc.tree.walk())[3]
    t = w.doc.add_task("t", [bone])
    w.show_tasks()
    w.tasks_window.open_task(t)
    from fishy.views.tasks import _open_editors

    ed = _open_editors[(w.doc, t)]
    ed.boneClicked.emit(bone)
    assert w.tree.current_bone() == bone
    ed.close()


# I4: unticking "No date" starts from today, not 2000-01-01.
def test_unticking_no_date_uses_today(qtbot, doc):
    from fishy.views.tasks import TaskEditor

    t = doc.add_task("t")
    ed = TaskEditor(doc, t)
    qtbot.addWidget(ed)
    ed.no_due.setChecked(False)
    assert doc.tasks[t].due_date == QDate.currentDate().toString("yyyy-MM-dd")


# I5: the Tasks window keeps its current row across changes (Ctrl+Shift+Enter repeats).
def test_tasks_window_keeps_current_row(qtbot, doc):
    from fishy.views.tasks import TasksWindow

    t = doc.add_task("t")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    w.table.setCurrentIndex(w.proxy.index(0, 2))
    w.cycle_current()
    w.cycle_current()
    assert doc.tasks[t].state_id == doc.ordered_task_states()[2].id
    assert w.current_task() == t


# I6: a failed CSV write (file open in Excel) shows a message instead of crashing.
def test_csv_export_error_is_reported(qtbot, doc, tmp_path, monkeypatch):
    from fishy.export import data
    from fishy.views.tasks import TasksWindow

    doc.add_task("t")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "x"), "CSV (*.csv)")))

    def boom(*a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(data, "write_tasks_csv", boom)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a[2])))
    w.export_csv()
    assert warned and "in use" in warned[0]


def test_csv_export_adds_suffix(qtbot, doc, tmp_path, monkeypatch):
    from fishy.views.tasks import TasksWindow

    doc.add_task("t")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "x"), "CSV (*.csv)")))
    w.export_csv()
    assert (tmp_path / "x.csv").exists()


# I7: the Due editor shows "No date" for a task without one.
def test_due_delegate_shows_no_date(qtbot, doc):
    from fishy.models.task_table_model import TaskCol
    from fishy.views.tasks import NO_DATE, TasksWindow, _DueDelegate

    doc.add_task("t")
    w = TasksWindow(doc)
    qtbot.addWidget(w)
    delegate = _DueDelegate()
    index = w.proxy.index(0, TaskCol.DUE)
    editor = delegate.createEditor(w.table, None, index)
    delegate.setEditorData(editor, index)
    assert editor.date() == NO_DATE


# Promoted minor: cells that Excel would read as formulas are neutralised.
def test_csv_neutralises_formula_cells(tmp_path, doc):
    from fishy.export.data import write_tasks_csv

    t = doc.add_task("-40 °C soak test", notes="=SUM(A1)", assignee="@ops")
    p = tmp_path / "t.csv"
    write_tasks_csv(doc, p, [t])
    row = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"), newline="")))[1]
    assert row[3] == "'-40 °C soak test" and row[9] == "'=SUM(A1)" and row[5] == "'@ops"
