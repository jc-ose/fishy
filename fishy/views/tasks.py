"""Task UI (spec §4.4 – 4.10): state menu, pickers, task editor, per-bone section,
and the all-tasks window.

Every change goes through `ProjectDocument` commands, so it is undoable and
written through immediately. Views refresh on `doc.tasksChanged`.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QDate, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
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
            self.state.addItem(theme.glyph_icon(st.symbol, st.color, 18), st.name, st.id)
        self.state.setCurrentIndex(self.state.findData(task.state_id))
        self.assignee.clear()
        self.assignee.addItems(self.doc.assignees())
        self.assignee.setEditText(task.assignee or "")
        self.no_due.setChecked(task.due_date is None)
        self.due.setEnabled(task.due_date is not None)
        if task.due_date:
            self.due.setDate(QDate.fromString(task.due_date, "yyyy-MM-dd"))
        else:  # unticking "No date" starts from today
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


_open_editors: dict[tuple[ProjectDocument, int], TaskEditor] = {}


def main_window_of(widget: QWidget | None) -> QWidget | None:
    """The main window (the one with `select_bone`) above `widget`, across dialogs."""
    w = widget
    while w is not None:
        if hasattr(w, "select_bone") and hasattr(w, "open_entry"):
            return w
        w = w.parentWidget()
    return None


def close_task_editors(doc: ProjectDocument) -> None:
    """Close (saving typed text) every task editor of `doc`, e.g. before it closes."""
    for key in [k for k in _open_editors if k[0] is doc]:
        ed = _open_editors.pop(key)
        ed.done(QDialog.DialogCode.Rejected)


def open_task_editor(doc: ProjectDocument, task_id: int, parent: QWidget | None) -> TaskEditor:
    """Show (or raise) the one editor for this task. Wires bone/entry navigation to the
    main window when `parent` lives in one."""
    key = (doc, task_id)
    ed = _open_editors.get(key)
    if ed is None:
        win = main_window_of(parent)
        ed = TaskEditor(doc, task_id, win or (parent.window() if parent else None))
        ed.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        _open_editors[key] = ed
        ed.destroyed.connect(lambda *_: _open_editors.pop(key, None))
        ed.finished.connect(lambda *_: _open_editors.pop(key, None))
        if win is not None:
            ed.boneClicked.connect(win.select_bone)
            ed.openEntry.connect(win.open_entry)
    ed.show()
    ed.raise_()
    ed.activateWindow()
    return ed


SHOW_DONE_KEY = "tasks/show_done"
OVERDUE_COLOR = "#d32f2f"
MUTED_COLOR = "#9e9e9e"


def confirm_delete_task(doc: ProjectDocument, task_id: int, parent: QWidget | None) -> bool:
    """Delete a task, asking first when log entries reference it (spec §4.7)."""
    refs = doc.entries_referencing_task(task_id)
    if refs:
        n = len(refs)
        answer = QMessageBox.question(
            parent, "Delete task",
            f"“{doc.tasks[task_id].title}” is referenced in {n} log entr"
            f"{'y' if n == 1 else 'ies'}. Delete it anyway?\n\nYou can undo this.")
        if answer != QMessageBox.StandardButton.Yes:
            return False
    return doc.delete_task(task_id)


class BoneTasksSection(QWidget):
    """The Tasks section of the details panel (spec §4.5)."""

    openTask = Signal(int)
    FADE_MS = 1500
    COL_SYMBOL, COL_TITLE, COL_ASSIGNEE, COL_DUE, COL_FLAG = range(5)

    def __init__(self, settings: QSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.doc: ProjectDocument | None = None
        self.bone_id: int | None = None
        self._fading: set[int] = set()
        self._shown_open: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 12)  # breathing room above the evidence log
        head = QHBoxLayout()
        self.header = QLabel("Tasks")
        self.header.setStyleSheet("font-weight: 600;")
        head.addWidget(self.header)
        head.addStretch(1)
        self.show_done = QToolButton()
        self.show_done.setCheckable(True)
        self.show_done.setAutoRaise(True)
        self.show_done.setChecked(bool(settings.value(SHOW_DONE_KEY, False, type=bool))
                                  if settings is not None else False)
        self.show_done.toggled.connect(self._show_done_toggled)
        head.addWidget(self.show_done)
        self.add_btn = QPushButton("+ Add")
        self.add_btn.clicked.connect(self.add_task)
        self.link_btn = QPushButton("Link existing…")
        self.link_btn.clicked.connect(self.link_existing)
        head.addWidget(self.add_btn)
        head.addWidget(self.link_btn)
        layout.addLayout(head)

        self.list = QTreeWidget()
        self.list.setColumnCount(5)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setIndentation(0)
        self.list.setUniformRowHeights(True)
        self.list.setIconSize(QSize(20, 20))
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        header = self.list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(self.COL_TITLE, header.ResizeMode.Stretch)
        for col in (self.COL_SYMBOL, self.COL_ASSIGNEE, self.COL_DUE, self.COL_FLAG):
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        layout.addWidget(self.list)
        self.empty_label = QLabel()
        theme.mark(self.empty_label, muted=True)
        layout.addWidget(self.empty_label)
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.Shape.HLine)
        theme.mark(self.separator, separator=True)
        layout.addSpacing(4)
        layout.addWidget(self.separator)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self.list, activated=self.cycle_current,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self._fade_timer = QTimer(self, singleShot=True, interval=self.FADE_MS)
        self._fade_timer.timeout.connect(self._fade_done)

    # ----- wiring --------------------------------------------------------- #

    def set_document(self, doc: ProjectDocument | None) -> None:
        self.doc = doc
        self.bone_id = None
        if doc is not None:
            doc.tasksChanged.connect(self.refresh)
            doc.changed.connect(self.refresh)  # numbers and titles in group headers
        self.refresh()

    def show_bone(self, bone_id: int | None) -> None:
        if bone_id != self.bone_id:
            self._fading.clear()
            self._shown_open = set()
        self.bone_id = bone_id
        self.refresh()

    # ----- display -------------------------------------------------------- #

    def refresh(self, *_args) -> None:
        keep = self._current()
        self.list.clear()
        self._populate()
        if keep is not None and (item := self.item_for(keep)) is not None:
            self.list.setCurrentItem(item)

    def _populate(self) -> None:
        doc, bid = self.doc, self.bone_id
        if doc is None or bid is None or bid not in doc.tree.bones:
            return
        show_done = self.show_done.isChecked()
        groups = doc.task_groups(bid)
        all_ids = [t for _, ids in groups for t in ids]
        done_ids = {t for t in all_ids if doc.is_task_done(t)}
        # A task just marked done stays (muted) for a moment so it can still be undone.
        if not show_done:
            newly = (self._shown_open & done_ids) - self._fading
            if newly:
                self._fading |= newly
                self._fade_timer.start()
        self._fading &= done_ids
        visible_done = done_ids if show_done else self._fading
        self._shown_open = set(all_ids) - done_ids
        n_open = len(self._shown_open)
        self.header.setText(f"Tasks ({n_open} open)" if all_ids else "Tasks")
        hidden = len(done_ids - visible_done)
        self.show_done.setText(f"Show done ({hidden})" if not show_done
                               else f"Hide done ({len(done_ids)})")
        self.show_done.setVisible(bool(done_ids))
        bold = QFont(self.list.font())
        bold.setBold(True)
        for group_bone, ids in groups:
            rows = [t for t in ids if t not in done_ids or t in visible_done]
            if not rows:
                continue
            if group_bone != bid:
                head = QTreeWidgetItem(["", _bone_label(doc, group_bone)])
                head.setFlags(Qt.ItemFlag.ItemIsEnabled)
                head.setFont(self.COL_TITLE, bold)
                self.list.addTopLevelItem(head)
                head.setFirstColumnSpanned(False)
            for t in rows:
                self.list.addTopLevelItem(self._row(t, faded=t in self._fading))
        rows = self.list.topLevelItemCount()
        self.list.setVisible(rows > 0)
        self._fit_list_height(rows)
        is_cat = doc.tree.bones[bid].is_category
        if rows:
            self.empty_label.hide()
        else:
            self.empty_label.setText(
                "All tasks are done." if all_ids else
                "No tasks on this category or its causes yet." if is_cat else
                "No tasks linked to this bone yet.")
            self.empty_label.show()

    MAX_ROWS = 8

    def _fit_list_height(self, rows: int) -> None:
        """Size the list to its rows (up to MAX_ROWS, then it scrolls): no empty box."""
        if not rows:
            return
        row_h = max(self.list.sizeHintForRow(0), self.list.iconSize().height() + 6)
        frame = 2 * self.list.frameWidth()
        self.list.setFixedHeight(min(rows, self.MAX_ROWS) * row_h + frame + 2)

    def _row(self, task_id: int, faded: bool) -> QTreeWidgetItem:
        doc = self.doc
        task = doc.tasks[task_id]
        st = doc.task_states.get(task.state_id)
        done = doc.is_task_done(task_id)
        item = QTreeWidgetItem(["", task.title, task.assignee or "—",
                                task.due_date or "", "⚑" if task.critical else ""])
        item.setData(self.COL_SYMBOL, Qt.ItemDataRole.UserRole, task_id)
        item.setIcon(self.COL_SYMBOL, theme.glyph_icon(st.symbol if st else "☐",
                                                       st.color if st else MUTED_COLOR, 20))
        item.setToolTip(self.COL_SYMBOL, f"{st.name if st else ''} — click to toggle, "
                        "right-click for all states")
        item.setToolTip(self.COL_TITLE, task.notes or task.title)
        if done or faded:
            font = item.font(self.COL_TITLE)
            font.setStrikeOut(True)
            item.setFont(self.COL_TITLE, font)
            for col in range(1, 5):
                item.setForeground(col, QBrush(QColor(MUTED_COLOR)))
        elif doc.is_task_overdue(task_id):
            item.setForeground(self.COL_DUE, QBrush(QColor(OVERDUE_COLOR)))
            item.setToolTip(self.COL_DUE, "Overdue")
        if task.critical:
            item.setForeground(self.COL_FLAG, QBrush(QColor(OVERDUE_COLOR)))
            item.setToolTip(self.COL_FLAG, "Critical")
        return item

    def visible_task_ids(self) -> list[int]:
        out = []
        for i in range(self.list.topLevelItemCount()):
            t = self.list.topLevelItem(i).data(self.COL_SYMBOL, Qt.ItemDataRole.UserRole)
            if t is not None:
                out.append(t)
        return out

    def item_for(self, task_id: int) -> QTreeWidgetItem | None:
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            if item.data(self.COL_SYMBOL, Qt.ItemDataRole.UserRole) == task_id:
                return item
        return None

    def _fade_done(self) -> None:
        self._fading.clear()
        self.refresh()

    def _show_done_toggled(self, on: bool) -> None:
        if self.settings is not None:
            self.settings.setValue(SHOW_DONE_KEY, on)
        self._fading.clear()
        self.refresh()

    # ----- actions -------------------------------------------------------- #

    def _task_at(self, item: QTreeWidgetItem | None) -> int | None:
        return None if item is None else item.data(self.COL_SYMBOL, Qt.ItemDataRole.UserRole)

    def _current(self) -> int | None:
        return self._task_at(self.list.currentItem())

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        task_id = self._task_at(item)
        if task_id is not None and column == self.COL_SYMBOL:
            self.doc.toggle_task(task_id)

    def _double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        task_id = self._task_at(item)
        if task_id is not None and column != self.COL_SYMBOL:
            self._open(task_id)

    def _open(self, task_id: int) -> None:
        self.openTask.emit(task_id)
        open_task_editor(self.doc, task_id, self)

    def cycle_current(self) -> None:
        task_id = self._current()
        if task_id is not None:
            self.doc.cycle_task(task_id)

    def unlink_current(self) -> None:
        task_id = self._current()
        if task_id is not None and self.bone_id in self.doc.tasks[task_id].bone_ids:
            self.doc.unlink_task(task_id, self.bone_id)

    def add_task(self) -> None:
        if self.doc is None or self.bone_id is None:
            return
        task_id = self.doc.add_task("New task", [self.bone_id])
        if task_id is not None:
            self._open(task_id)

    def link_existing(self) -> None:
        if self.doc is None or self.bone_id is None:
            return
        direct = self.doc.task_links.get(self.bone_id, set())
        task_id = pick_task(self.doc, self, exclude=set(direct))
        if task_id is not None:
            self.doc.link_task(task_id, self.bone_id)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        task_id = self._task_at(item)
        if task_id is None:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._open(task_id))
        states = state_menu(self.doc, task_id, menu)
        states.setTitle("State")
        menu.addMenu(states)
        if self.bone_id in self.doc.tasks[task_id].bone_ids:
            menu.addAction("Unlink from this bone", self.unlink_current)
        menu.addSeparator()
        menu.addAction("Delete task…", lambda: confirm_delete_task(self.doc, task_id, self))
        menu.exec(self.list.viewport().mapToGlobal(pos))


# =========================================================================== #
# Tasks window (all tasks)
# =========================================================================== #


NO_DATE = QDate(2000, 1, 1)  # the date editor's minimum, shown as "No date"


class _AssigneeDelegate(QStyledItemDelegate):
    def __init__(self, doc: ProjectDocument, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self.doc.assignees())
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setEditText(index.data(Qt.ItemDataRole.EditRole) or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class _DueDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        edit = QDateEdit(parent)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setMinimumDate(NO_DATE)
        edit.setSpecialValueText("No date")
        return edit

    def setEditorData(self, editor, index) -> None:
        value = index.data(Qt.ItemDataRole.EditRole)
        editor.setDate(value if isinstance(value, QDate) and value.isValid() else NO_DATE)

    def setModelData(self, editor, model, index) -> None:
        date = editor.date()
        model.setData(index, None if date == NO_DATE else date, Qt.ItemDataRole.EditRole)


class TasksWindow(QDialog):
    """Every task in the project: sortable, filterable, editable (spec §4.10)."""

    boneSelected = Signal(int)
    openEntry = Signal(int, int)

    def __init__(self, doc: ProjectDocument, parent=None) -> None:
        from fishy.models.task_table_model import TaskCol, TaskFilterProxy, TaskTableModel

        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Tasks")
        self.resize(1000, 560)
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, notes, assignee")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 2)
        self.hide_done = QCheckBox("Hide done")
        self.hide_done.setChecked(True)
        bar.addWidget(self.hide_done)
        self.assignee_filter = QComboBox()
        self.assignee_filter.setMinimumWidth(140)
        bar.addWidget(self.assignee_filter)
        self.critical_only = QCheckBox("Critical only")
        self.overdue_only = QCheckBox("Overdue only")
        self.unlinked_only = QCheckBox("Unlinked only")
        for box in (self.critical_only, self.overdue_only, self.unlinked_only):
            bar.addWidget(box)
        layout.addLayout(bar)

        self.model = TaskTableModel(doc, self)
        self.proxy = TaskFilterProxy(doc, self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(TaskCol.ID, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(20, 20))
        self.table.setItemDelegateForColumn(TaskCol.ASSIGNEE, _AssigneeDelegate(doc, self))
        self.table.setItemDelegateForColumn(TaskCol.DUE, _DueDelegate(self))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(TaskCol.TITLE, header.ResizeMode.Stretch)
        for col, width in ((TaskCol.ID, 50), (TaskCol.STATE, 130), (TaskCol.BONES, 110),
                           (TaskCol.ASSIGNEE, 130), (TaskCol.DUE, 95),
                           (TaskCol.CRITICAL, 34), (TaskCol.UPDATED, 125)):
            header.resizeSection(col, width)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.clicked.connect(self._clicked)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)
        self._keep: int | None = None
        self.model.modelAboutToBeReset.connect(self._remember_current)
        self.model.modelReset.connect(self._restore_current)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self.table, activated=self.cycle_current,
                  context=Qt.ShortcutContext.WidgetShortcut)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.summary = QLabel()
        bottom.addWidget(self.summary)
        bottom.addStretch(1)
        hint = QLabel("Click a checkbox to toggle · right-click for all states · "
                      "double-click to edit")
        theme.mark(hint, muted=True)
        bottom.addWidget(hint)
        self.new_btn = QPushButton("+ New task")
        self.new_btn.clicked.connect(self.new_task)
        self.export_btn = QPushButton("Export CSV…")
        self.export_btn.clicked.connect(self.export_csv)
        bottom.addWidget(self.new_btn)
        bottom.addWidget(self.export_btn)
        layout.addLayout(bottom)

        self.search.textChanged.connect(self.proxy.set_text)
        self.hide_done.toggled.connect(self.proxy.set_hide_done)
        self.critical_only.toggled.connect(self.proxy.set_critical_only)
        self.overdue_only.toggled.connect(self.proxy.set_overdue_only)
        self.unlinked_only.toggled.connect(self.proxy.set_unlinked_only)
        self.assignee_filter.activated.connect(
            lambda _i: self.proxy.set_assignee(self.assignee_filter.currentData()))
        doc.tasksChanged.connect(self.refresh)
        self.refresh()

    # ----- display -------------------------------------------------------- #

    def refresh(self, *_args) -> None:
        doc = self.doc
        ids = list(doc.tasks)
        open_ids = [t for t in ids if not doc.is_task_done(t)]
        overdue = sum(1 for t in open_ids if doc.is_task_overdue(t))
        critical = sum(1 for t in open_ids if doc.tasks[t].critical)
        self.summary.setText(f"{len(ids)} task{'s' if len(ids) != 1 else ''} · "
                             f"{len(open_ids)} open · {overdue} overdue · {critical} critical")
        current = self.assignee_filter.currentData()
        self.assignee_filter.blockSignals(True)
        self.assignee_filter.clear()
        self.assignee_filter.addItem("All assignees", None)
        self.assignee_filter.addItem("Unassigned", "")
        for name in doc.assignees():
            self.assignee_filter.addItem(name, name)
        i = self.assignee_filter.findData(current)
        self.assignee_filter.setCurrentIndex(max(0, i))
        self.assignee_filter.blockSignals(False)

    # ----- actions -------------------------------------------------------- #

    def _task_at(self, index) -> int | None:
        from fishy.models.task_table_model import TaskIdRole

        return index.data(TaskIdRole) if index.isValid() else None

    def current_task(self) -> int | None:
        return self._task_at(self.table.currentIndex())

    def _remember_current(self) -> None:
        self._keep = self.current_task()

    def _restore_current(self) -> None:
        if self._keep is None or self._keep not in self.doc.tasks:
            return
        row = self.model.row_for(self._keep)
        col = max(self.table.currentIndex().column(), 0)
        index = self.proxy.mapFromSource(self.model.index(row, col))
        if index.isValid():
            self.table.selectionModel().blockSignals(True)
            self.table.setCurrentIndex(index)
            self.table.selectionModel().blockSignals(False)
            self.table.scrollTo(index)

    def _clicked(self, index) -> None:
        from fishy.models.task_table_model import TaskCol

        task_id = self._task_at(index)
        if task_id is not None and index.column() == TaskCol.STATE:
            self.doc.toggle_task(task_id)

    def _double_clicked(self, index) -> None:
        from fishy.models.task_table_model import TaskCol

        task_id = self._task_at(index)
        if task_id is None or index.column() == TaskCol.STATE:
            return
        if not (index.flags() & Qt.ItemFlag.ItemIsEditable):
            self.open_task(task_id)

    def _row_changed(self, current, _previous) -> None:
        task_id = self._task_at(current)
        if task_id is not None and len(self.doc.tasks[task_id].bone_ids) == 1:
            self.boneSelected.emit(self.doc.tasks[task_id].bone_ids[0])

    def open_task(self, task_id: int) -> None:
        ed = open_task_editor(self.doc, task_id, self)
        if main_window_of(self) is None:  # standalone window: let the caller navigate
            ed.boneClicked.connect(self.boneSelected)
            ed.openEntry.connect(self.openEntry)

    def cycle_current(self) -> None:
        task_id = self.current_task()
        if task_id is not None:
            self.doc.cycle_task(task_id)

    def new_task(self) -> None:
        task_id = self.doc.add_task("New task")
        if task_id is not None:
            self.open_task(task_id)

    def export_csv(self) -> None:
        from fishy.export import data

        path, _ = QFileDialog.getSaveFileName(self, "Export tasks", "tasks.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            data.write_tasks_csv(self.doc, path, self.proxy.visible_task_ids())
        except OSError as exc:
            QMessageBox.warning(self, "Export tasks",
                                f"Couldn't write {path}:\n{exc}\n\nIs it open in another "
                                "program (e.g. Excel)?")

    def _context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        task_id = self._task_at(index)
        if task_id is None:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.open_task(task_id))
        states = state_menu(self.doc, task_id, menu)
        states.setTitle("State")
        menu.addMenu(states)
        menu.addSeparator()
        menu.addAction("Delete task…", lambda: confirm_delete_task(self.doc, task_id, self))
        menu.exec(self.table.viewport().mapToGlobal(pos))
