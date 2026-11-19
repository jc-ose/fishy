"""Tasks: widgets (state menu, entry links, editor buttons, dialogs, sections)."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog

from fishy.views.evidence import EntryEditor, EvidenceLog, make_markdown_view
from fishy.views.tasks import state_menu


def _patch_exec(monkeypatch, text, convert=True, accept=True):
    def exec_(self):
        self.source.setPlainText(text)
        if convert:
            self.source.selectAll()
            self.convert_to_task()
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(EntryEditor, "exec", exec_)


def test_editor_convert_rewrites_only_checkbox_lines(qtbot, doc_with_cause):
    doc, cause = doc_with_cause
    ed = EntryEditor(doc, cause)
    qtbot.addWidget(ed)
    ed.source.setPlainText("intro\n- [ ] Pull records\n")
    cursor = ed.source.textCursor()
    cursor.setPosition(ed.source.toPlainText().index("Pull"))
    ed.source.setTextCursor(cursor)
    assert ed.convert_to_task() == 1
    assert ed.body() == "intro\n- [[task:new]] Pull records\n"
    cursor.setPosition(1)
    ed.source.setTextCursor(cursor)
    assert ed.convert_to_task() == 0 and "No checkbox" in ed.hint.text()


def test_cancelled_editor_creates_no_task(qtbot, doc_with_cause, tmp_settings, monkeypatch):
    doc, cause = doc_with_cause
    log = EvidenceLog(tmp_settings)
    qtbot.addWidget(log)
    log.set_document(doc)
    log.show_bone(cause)
    _patch_exec(monkeypatch, "- [ ] Pull records\n", accept=False)
    assert log.add_entry() is None and doc.tasks == {}


def test_saved_editor_creates_task(qtbot, doc_with_cause, tmp_settings, monkeypatch):
    doc, cause = doc_with_cause
    log = EvidenceLog(tmp_settings)
    qtbot.addWidget(log)
    log.set_document(doc)
    log.show_bone(cause)
    _patch_exec(monkeypatch, "- [ ] Pull records\n")
    eid = log.add_entry()
    (t,) = doc.tasks.values()
    assert t.title == "Pull records" and t.bone_ids == [cause]
    assert doc.repo.log_entry(eid).body_md == f"- [[task:{t.id}]]\n"
    _patch_exec(monkeypatch, f"- [[task:{t.id}]]\n- [x] Second\n")
    assert log.edit_entry(eid)
    assert {x.title for x in doc.tasks.values()} == {"Pull records", "Second"}


def test_view_toggle_link(qtbot, doc_with_cause):
    doc, cause = doc_with_cause
    t = doc.add_task("x", [cause])
    view = make_markdown_view(doc)
    qtbot.addWidget(view)
    view.set_markdown(f"[[task:{t}]]")
    assert "x" in view.toPlainText()
    view._link_clicked(QUrl(f"fishy-task-toggle:{t}"))
    assert doc.is_task_done(t)
    assert "☑" in view.toPlainText()  # re-rendered on tasksChanged


def test_view_title_link_emits_task_clicked(qtbot, doc_with_cause):
    doc, cause = doc_with_cause
    t = doc.add_task("x", [cause])
    view = make_markdown_view(doc)
    qtbot.addWidget(view)
    with qtbot.waitSignal(view.taskClicked) as blocker:
        view._link_clicked(QUrl(f"fishy-task:{t}"))
    assert blocker.args == [t]


def test_state_menu_lists_states(qtbot, doc_with_cause):
    doc, _ = doc_with_cause
    t = doc.add_task("x")
    m = state_menu(doc, t, None)
    assert [a.text() for a in m.actions()][:2] == ["☐  To do", "◐  In progress"]
    assert m.actions()[0].isChecked()
    m.actions()[2].trigger()
    assert doc.tasks[t].state_id == doc.ordered_task_states()[2].id


def test_timeline_summary_shows_task_title(qtbot, doc_with_cause):
    from fishy.views.evidence import TimelineDialog

    doc, cause = doc_with_cause
    t = doc.add_task("Pull records", [cause])
    doc.add_log_entry(cause, f"- [[task:{t}]]")
    dlg = TimelineDialog(doc)
    qtbot.addWidget(dlg)
    assert dlg.tree.topLevelItem(0).text(2) == "Pull records"


def test_task_states_tab_add_and_edit(qtbot, doc, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from fishy.views.choices_dialog import ChoicesDialog

    dlg = ChoicesDialog(doc)
    qtbot.addWidget(dlg)
    tab = dlg.task_states_tab
    assert dlg.tabs.tabText(2) == "Task states" and tab.list.count() == 4
    tab.list.setCurrentRow(1)
    tab.symbol_edit.setText("▶")
    tab.symbol_edit.editingFinished.emit()
    assert doc.ordered_task_states()[1].symbol == "▶"
    assert tab.usage.text() == "Used by 0 tasks"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Waiting", True)))
    tab._add()
    assert len(doc.task_states) == 5
    assert doc.ordered_task_states()[2].name == "Waiting"


def test_task_states_tab_reports_done_rule(qtbot, doc):
    from fishy.views.choices_dialog import ChoicesDialog

    dlg = ChoicesDialog(doc)
    qtbot.addWidget(dlg)
    tab = dlg.task_states_tab
    tab.list.setCurrentRow(3)  # Done
    errors = []
    doc.error.connect(errors.append)
    tab.role.setCurrentIndex(tab.role.findData("active"))
    tab.role.activated.emit(tab.role.currentIndex())
    assert errors and "must count as done" in errors[0]
    assert tab.role.currentData() == "done"  # reverted on screen


def test_task_editor_edits_fields(qtbot, doc_with_cause):
    from PySide6.QtCore import QDate

    from fishy.views.tasks import TaskEditor

    doc, cause = doc_with_cause
    t = doc.add_task("x", [cause])
    ed = TaskEditor(doc, t)
    qtbot.addWidget(ed)
    ed.assignee.setEditText("  A. Lee ")
    ed.assignee.lineEdit().editingFinished.emit()
    ed.no_due.setChecked(False)
    ed.due.setDate(QDate(2026, 11, 24))
    ed.critical.setChecked(True)
    ed.title.setText("")
    ed.title.editingFinished.emit()
    task = doc.tasks[t]
    assert (task.assignee, task.due_date, task.critical, task.title) == \
        ("A. Lee", "2026-11-24", True, "x")
    assert ed.title.text() == "x"
    ed.no_due.setChecked(True)
    assert doc.tasks[t].due_date is None


def test_task_editor_state_notes_links_refs(qtbot, doc_with_cause, monkeypatch):
    from fishy.views import tasks as tasks_view
    from fishy.views.tasks import TaskEditor

    doc, cause = doc_with_cause
    cat = doc.tree.roots()[0]
    t = doc.add_task("x", [cause])
    doc.add_log_entry(cause, f"see [[task:{t}]]")
    ed = TaskEditor(doc, t)
    qtbot.addWidget(ed)
    assert ed.refs.count() == 1 and ed.bones.count() == 1
    ed.state.setCurrentIndex(1)
    ed.state.activated.emit(1)
    assert doc.tasks[t].state_id == doc.ordered_task_states()[1].id
    ed.notes.setPlainText("some notes")
    ed.commit_notes()
    assert doc.tasks[t].notes == "some notes"
    monkeypatch.setattr(tasks_view, "_pick_bone", lambda *a, **k: cat)
    ed.add_bone_btn.click()
    assert sorted(doc.tasks[t].bone_ids) == sorted([cause, cat]) and ed.bones.count() == 2
    ed.bones.setCurrentRow(0)
    ed.remove_bone_btn.click()
    assert len(doc.tasks[t].bone_ids) == 1


def test_task_editor_closes_when_task_deleted(qtbot, doc):
    from fishy.views.tasks import _open_editors, open_task_editor

    t = doc.add_task("x")
    ed = open_task_editor(doc, t, None)  # deletes itself on close
    assert open_task_editor(doc, t, None) is ed  # one editor per task
    doc.delete_task(t)
    assert (id(doc), t) not in _open_editors


def _section(qtbot, doc, settings, bone_id):
    from fishy.views.tasks import BoneTasksSection

    sec = BoneTasksSection(settings)
    qtbot.addWidget(sec)
    sec.set_document(doc)
    sec.show_bone(bone_id)
    return sec


def test_section_hides_done_and_toggle_shows(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    a = doc.add_task("a", [cause])
    b = doc.add_task("b", [cause])
    doc.toggle_task(b)
    sec = _section(qtbot, doc, tmp_settings, cause)
    assert sec.visible_task_ids() == [a] and sec.show_done.text() == "Show done (1)"
    sec.show_done.setChecked(True)
    assert sec.visible_task_ids() == [a, b]
    assert tmp_settings.value("tasks/show_done", type=bool)


def test_done_row_fades_then_hides(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    a = doc.add_task("a", [cause])
    sec = _section(qtbot, doc, tmp_settings, cause)
    doc.toggle_task(a)
    assert sec.visible_task_ids() == [a]
    qtbot.waitUntil(lambda: sec.visible_task_ids() == [], timeout=3000)
    assert sec.show_done.text() == "Show done (1)"


def test_category_groups(qtbot, doc, tmp_settings):
    cat = doc.tree.roots()[0]
    c1 = doc.add_bone(cat, "c1")
    d = doc.add_task("direct", [cat])
    s = doc.add_task("sub", [c1, cat])
    only_sub = doc.add_task("only sub", [c1])
    sec = _section(qtbot, doc, tmp_settings, cat)
    assert sec.visible_task_ids() == [d, s, only_sub]  # s listed once, as a direct link
    assert sec.header.text() == "Tasks (3 open)"
    headers = [sec.list.topLevelItem(i).text(1) for i in range(sec.list.topLevelItemCount())
               if sec.list.topLevelItem(i).data(0, 256) is None]
    assert headers == ["1.1 c1"]


def test_section_click_symbol_toggles_and_add(qtbot, doc_with_cause, tmp_settings, monkeypatch):
    from fishy.views import tasks as tasks_view

    doc, cause = doc_with_cause
    a = doc.add_task("a", [cause])
    sec = _section(qtbot, doc, tmp_settings, cause)
    sec.list.itemClicked.emit(sec.item_for(a), 0)
    assert doc.is_task_done(a)
    opened = []
    monkeypatch.setattr(tasks_view, "open_task_editor", lambda d, t, p: opened.append(t))
    sec.add_btn.click()
    (new,) = set(doc.tasks) - {a}
    assert doc.tasks[new].bone_ids == [cause] and opened == [new]


def test_section_unlink_and_cycle(qtbot, doc_with_cause, tmp_settings):
    doc, cause = doc_with_cause
    a = doc.add_task("a", [cause])
    sec = _section(qtbot, doc, tmp_settings, cause)
    sec.list.setCurrentItem(sec.item_for(a))
    sec.cycle_current()
    assert doc.tasks[a].state_id == doc.ordered_task_states()[1].id
    sec.unlink_current()
    assert doc.tasks[a].bone_ids == [] and sec.visible_task_ids() == []


def test_details_panel_has_tasks_section(qtbot, doc_with_cause, tmp_settings):
    from fishy.views.details_panel import DetailsPanel

    doc, cause = doc_with_cause
    t = doc.add_task("a", [cause])
    panel = DetailsPanel(settings=tmp_settings)
    qtbot.addWidget(panel)
    panel.set_document(doc)
    panel.show_bone(cause)
    assert panel.tasks.visible_task_ids() == [t]
    panel.show_bone(doc.tree.roots()[0])
    assert panel.tasks.visible_task_ids() == [t]  # category rolls up
