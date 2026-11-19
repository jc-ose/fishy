"""Task UI (spec §4.4 – 4.10): state menu, pickers, task editor, per-bone section,
and the all-tasks window.

Every change goes through `ProjectDocument` commands, so it is undoable and
written through immediately. Views refresh on `doc.tasksChanged`.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.domain.tasks import default_state_id, done_state_id
from fishy.markdown_render import TaskInfo, TaskLabel, summary_line
from fishy.models.bone_tree_model import local_time


def task_info_fn(doc: ProjectDocument) -> TaskLabel:
    """Task id → what a rendered ``[[task:N]]`` shows (None once deleted)."""

    def info(task_id: int) -> TaskInfo | None:
        task = doc.tasks.get(task_id)
        if task is None:
            return None
        st = doc.task_states.get(task.state_id)
        return TaskInfo(st.symbol if st else "☐", st.color if st else "#888888", task.title,
                        doc.is_task_done(task_id))

    return info


def pending_symbols(doc: ProjectDocument) -> tuple[str, str]:
    """Symbols for an unsaved ``[[task:new]]`` / ``[[task:new:x]]`` in the preview."""
    states = doc.task_states
    return (states[default_state_id(states)].symbol, states[done_state_id(states)].symbol)


def state_menu(doc: ProjectDocument, task_id: int, parent: QWidget | None) -> QMenu:
    """Every task state, current one checked; choosing one sets it (spec §4.4)."""
    menu = QMenu(parent)
    group = QActionGroup(menu)
    current = doc.tasks[task_id].state_id
    for st in doc.ordered_task_states():
        act = QAction(f"{st.symbol}  {st.name}", menu, checkable=True)
        act.setChecked(st.id == current)
        act.triggered.connect(lambda _=False, sid=st.id: doc.set_task_state(task_id, sid))
        group.addAction(act)
        menu.addAction(act)
    return menu


def task_label_text(doc: ProjectDocument, task_id: int) -> str:
    task = doc.tasks[task_id]
    st = doc.task_states.get(task.state_id)
    return f"{st.symbol if st else '☐'}  #{task_id}  {task.title}"


class _PickerDialog(QDialog):
    """A filterable list of choices; returns the chosen item's data."""

    def __init__(self, title: str, items: list[tuple[str, int]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to filter")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = QListWidget()
        for text, data in items:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemActivated.connect(lambda _item: self.accept())
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        needle = text.casefold()
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hidden = needle not in item.text().casefold()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = i
        if first is not None:
            self.list.setCurrentRow(first)

    def chosen(self) -> int | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(Qt.ItemDataRole.UserRole)


def pick_task(doc: ProjectDocument, parent: QWidget | None, exclude: set[int] = frozenset(),
              first: Iterable[int] = ()) -> int | None:
    """Let the user choose a task (`first` ids listed first); None if cancelled."""
    first = [t for t in first if t in doc.tasks and t not in exclude]
    rest = [t for t in sorted(doc.tasks) if t not in exclude and t not in first]
    items = [(task_label_text(doc, t), t) for t in first + rest]
    if not items:
        return None
    dlg = _PickerDialog("Choose a task", items, parent)
    return dlg.chosen() if dlg.exec() == QDialog.DialogCode.Accepted else None


def _pick_bone(doc: ProjectDocument, parent, exclude: set[int]) -> int | None:
    from fishy.views.evidence import pick_bone  # evidence imports this module

    return pick_bone(doc, parent, exclude)


def _bone_label(doc: ProjectDocument, bone_id: int) -> str:
    return f"{doc.numbers.get(bone_id, '')} {doc.bone(bone_id).title}".strip()


class _NotesEdit(QPlainTextEdit):
    def __init__(self, on_focus_out, parent=None) -> None:
        super().__init__(parent)
        self._on_focus_out = on_focus_out

    def focusOutEvent(self, e) -> None:
        self._on_focus_out()
        super().focusOutEvent(e)


class TaskEditor(QDialog):
    """Edit one task (spec §4.6). Non-modal; every field commits as you leave it."""

    boneClicked = Signal(int)
    openEntry = Signal(int, int)  # bone id, entry id

    def __init__(self, doc: ProjectDocument, task_id: int, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.task_id = task_id
        self.setWindowTitle(f"Task #{task_id}")
        self.resize(560, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.editingFinished.connect(self._commit_title)
        form.addRow("Title", self.title)
        self.state = QComboBox()
        self.state.activated.connect(self._state_chosen)
        form.addRow("State", self.state)
        self.assignee = QComboBox()
        self.assignee.setEditable(True)
        self.assignee.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.assignee.lineEdit().setPlaceholderText("Unassigned")
        self.assignee.lineEdit().editingFinished.connect(self._commit_assignee)
        self.assignee.activated.connect(lambda _i: self._commit_assignee())
        form.addRow("Assignee", self.assignee)
        self.due = QDateEdit()
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("yyyy-MM-dd")
        self.due.dateChanged.connect(self._commit_due)
        self.no_due = QCheckBox("No date")
        self.no_due.toggled.connect(self._commit_due)
        due_row = QHBoxLayout()
        due_row.addWidget(self.due)
        due_row.addWidget(self.no_due)
        due_row.addStretch(1)
        form.addRow("Due", due_row)
        self.critical = QCheckBox("Critical")
        self.critical.toggled.connect(self._commit_critical)
        form.addRow("", self.critical)
        layout.addLayout(form)

        layout.addWidget(QLabel("Notes"))
        self.notes = _NotesEdit(self.commit_notes)
        self.notes.setMaximumHeight(110)
        layout.addWidget(self.notes)

        layout.addWidget(QLabel("Linked bones"))
        self.bones = QListWidget()
        self.bones.setMaximumHeight(110)
        self.bones.itemActivated.connect(
            lambda item: self.boneClicked.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.bones)
        bone_buttons = QHBoxLayout()
        self.add_bone_btn = QPushButton("Add…")
        self.add_bone_btn.clicked.connect(self._add_bone)
        self.remove_bone_btn = QPushButton("Remove")
        self.remove_bone_btn.clicked.connect(self._remove_bone)
        bone_buttons.addWidget(self.add_bone_btn)
        bone_buttons.addWidget(self.remove_bone_btn)
        bone_buttons.addStretch(1)
        layout.addLayout(bone_buttons)

        layout.addWidget(QLabel("Referenced in"))
        self.refs = QListWidget()
        self.refs.setMaximumHeight(90)
        self.refs.itemActivated.connect(
            lambda item: self.openEntry.emit(*item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.refs)

        self.stamps = QLabel()
        theme.mark(self.stamps, muted=True, small=True)
        layout.addWidget(self.stamps)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        doc.tasksChanged.connect(self.refresh)
        self.refresh()

    # ----- display -------------------------------------------------------- #

    def refresh(self, *_args) -> None:
        task = self.doc.tasks.get(self.task_id)
        if task is None:  # deleted (perhaps by undo): nothing left to edit
            self.close()
            return
        widgets = (self.title, self.state, self.assignee, self.due, self.no_due,
                   self.critical, self.notes)
        for w in widgets:
            w.blockSignals(True)
        if not self.title.hasFocus():
            self.title.setText(task.title)
        self.state.clear()
        for st in self.doc.ordered_task_states():
            self.state.addItem(f"{st.symbol}  {st.name}", st.id)
        self.state.setCurrentIndex(self.state.findData(task.state_id))
        self.assignee.clear()
        self.assignee.addItems(self.doc.assignees())
        self.assignee.setEditText(task.assignee or "")
        self.no_due.setChecked(task.due_date is None)
        self.due.setEnabled(task.due_date is not None)
        if task.due_date:
            self.due.setDate(QDate.fromString(task.due_date, "yyyy-MM-dd"))
        elif self.due.date() == self.due.minimumDate():
            self.due.setDate(QDate.currentDate())
        self.critical.setChecked(task.critical)
        if not self.notes.hasFocus():
            self.notes.setPlainText(task.notes)
        for w in widgets:
            w.blockSignals(False)

        self.bones.clear()
        for bid in task.bone_ids:
            item = QListWidgetItem(_bone_label(self.doc, bid))
            item.setData(Qt.ItemDataRole.UserRole, bid)
            self.bones.addItem(item)
        self.remove_bone_btn.setEnabled(bool(task.bone_ids))
        self.refs.clear()
        info = task_info_fn(self.doc)
        for e in self.doc.entries_referencing_task(self.task_id):
            text = e.title or summary_line(e.body_md, task_label=info) or "(entry)"
            item = QListWidgetItem(f"{_bone_label(self.doc, e.bone_id)} — {text}")
            item.setData(Qt.ItemDataRole.UserRole, (e.bone_id, e.id))
            self.refs.addItem(item)
        users = self.doc.repo.users()

        def who(uid):
            return users[uid].display_name if uid in users else "?"

        self.stamps.setText(f"Created {local_time(task.created_at)} by {who(task.created_by)}"
                            f" · Updated {local_time(task.updated_at)} by "
                            f"{who(task.updated_by)}")

    # ----- edits ---------------------------------------------------------- #

    def _task(self):
        return self.doc.tasks.get(self.task_id)

    def _commit_title(self) -> None:
        task = self._task()
        if task is None:
            return
        text = self.title.text().strip()
        if text != task.title and not self.doc.update_task(self.task_id, title=text):
            self.title.setText(task.title)  # rejected (blank): put the old title back

    def _state_chosen(self, _index: int) -> None:
        task = self._task()
        sid = self.state.currentData()
        if task and sid is not None and sid != task.state_id:
            self.doc.set_task_state(self.task_id, sid)

    def _commit_assignee(self) -> None:
        task = self._task()
        name = self.assignee.currentText().strip() or None
        if task and name != task.assignee:
            self.doc.update_task(self.task_id, assignee=name)

    def _commit_due(self, *_args) -> None:
        task = self._task()
        if task is None:
            return
        due = None if self.no_due.isChecked() else self.due.date().toString("yyyy-MM-dd")
        self.due.setEnabled(due is not None)
        if due != task.due_date:
            self.doc.update_task(self.task_id, due_date=due)

    def _commit_critical(self, on: bool) -> None:
        task = self._task()
        if task and on != task.critical:
            self.doc.update_task(self.task_id, critical=on)

    def commit_notes(self) -> None:
        task = self._task()
        text = self.notes.toPlainText()
        if task and text != task.notes:
            self.doc.update_task(self.task_id, notes=text)

    def _add_bone(self) -> None:
        task = self._task()
        if task is None:
            return
        bone_id = _pick_bone(self.doc, self, set(task.bone_ids))
        if bone_id is not None:
            self.doc.link_task(self.task_id, bone_id)

    def _remove_bone(self) -> None:
        item = self.bones.currentItem()
        if item is not None:
            self.doc.unlink_task(self.task_id, item.data(Qt.ItemDataRole.UserRole))

    def done(self, result: int) -> None:
        self._commit_title()
        self.commit_notes()
        super().done(result)


_open_editors: dict[tuple[int, int], TaskEditor] = {}


def open_task_editor(doc: ProjectDocument, task_id: int, parent: QWidget | None) -> TaskEditor:
    """Show (or raise) the one editor for this task. Wires bone/entry navigation to the
    main window when `parent` lives in one."""
    key = (id(doc), task_id)
    ed = _open_editors.get(key)
    if ed is None:
        ed = TaskEditor(doc, task_id, parent.window() if parent is not None else None)
        ed.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        _open_editors[key] = ed
        ed.destroyed.connect(lambda *_: _open_editors.pop(key, None))
        ed.finished.connect(lambda *_: _open_editors.pop(key, None))
        win = parent.window() if parent is not None else None
        if win is not None and hasattr(win, "select_bone"):
            ed.boneClicked.connect(win.select_bone)
        if win is not None and hasattr(win, "open_entry"):
            ed.openEntry.connect(win.open_entry)
    ed.show()
    ed.raise_()
    ed.activateWindow()
    return ed
