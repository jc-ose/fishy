"""Task UI (spec §4.4 – 4.10): state menu, pickers, task editor, per-bone section,
and the all-tasks window.

Every change goes through `ProjectDocument` commands, so it is undoable and
written through immediately. Views refresh on `doc.tasksChanged`.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from fishy.document import ProjectDocument
from fishy.domain.tasks import default_state_id, done_state_id
from fishy.markdown_render import TaskInfo, TaskLabel


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
