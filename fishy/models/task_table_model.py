"""Table model and filter/sort proxy for the Tasks window (spec §4.10).

A flat model over `doc.tasks`, separate from the bone tree, so a
`QSortFilterProxyModel` is fine here. Edits go through document commands;
`setData` returns False when the document rejects one (e.g. a blank title).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QFont

from fishy import theme
from fishy.document import ProjectDocument
from fishy.domain.tasks import number_sort_key
from fishy.models.bone_tree_model import local_time

Index = QModelIndex | QPersistentModelIndex

OVERDUE_COLOR = "#d32f2f"
MUTED_COLOR = "#9e9e9e"


class TaskCol(IntEnum):
    ID = 0
    STATE = 1
    TITLE = 2
    BONES = 3
    ASSIGNEE = 4
    DUE = 5
    CRITICAL = 6
    UPDATED = 7


HEADERS = {TaskCol.ID: "#", TaskCol.STATE: "State", TaskCol.TITLE: "Title",
           TaskCol.BONES: "Bones", TaskCol.ASSIGNEE: "Assignee", TaskCol.DUE: "Due",
           TaskCol.CRITICAL: "⚑", TaskCol.UPDATED: "Updated"}

TaskIdRole = Qt.ItemDataRole.UserRole + 1
SortRole = Qt.ItemDataRole.UserRole + 2
StateIdRole = Qt.ItemDataRole.UserRole + 3

# State changes by click / right-click / Ctrl+Shift+Enter, not an inline editor.
EDITABLE = {TaskCol.TITLE, TaskCol.ASSIGNEE, TaskCol.DUE}


class TaskTableModel(QAbstractTableModel):
    def __init__(self, doc: ProjectDocument, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._ids: list[int] = sorted(doc.tasks)
        doc.tasksChanged.connect(self.reload)
        doc.changed.connect(self.reload)  # bone numbers and titles
        doc.choicesChanged.connect(self.reload)

    def reload(self, *_args) -> None:
        self.beginResetModel()
        self._ids = sorted(self.doc.tasks)
        self.endResetModel()

    def task_id(self, row: int) -> int:
        return self._ids[row]

    def row_for(self, task_id: int) -> int:
        return self._ids.index(task_id)

    # ----- shape ---------------------------------------------------------- #

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(TaskCol)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[TaskCol(section)]
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = TaskCol(index.column())
        if col in EDITABLE:
            flags |= Qt.ItemFlag.ItemIsEditable
        if col is TaskCol.CRITICAL:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    # ----- data ----------------------------------------------------------- #

    def _bone_numbers(self, bone_ids: list[int]) -> list[str]:
        return [self.doc.numbers.get(b, "") for b in bone_ids if b in self.doc.tree.bones]

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        doc = self.doc
        tid = self._ids[index.row()]
        task = doc.tasks.get(tid)
        if task is None:
            return None
        col = TaskCol(index.column())
        st = doc.task_states.get(task.state_id)
        done = doc.is_task_done(tid)
        if role == TaskIdRole:
            return tid
        if role == StateIdRole:
            return task.state_id
        if role == Qt.ItemDataRole.DisplayRole:
            if col is TaskCol.ID:
                return f"#{tid}"
            if col is TaskCol.STATE:
                return st.name if st else ""
            if col is TaskCol.TITLE:
                return task.title
            if col is TaskCol.BONES:
                return ", ".join(self._bone_numbers(task.bone_ids))
            if col is TaskCol.ASSIGNEE:
                return task.assignee or ""
            if col is TaskCol.DUE:
                return task.due_date or ""
            if col is TaskCol.UPDATED:
                return local_time(task.updated_at)
            return None
        if role == Qt.ItemDataRole.EditRole:
            if col is TaskCol.STATE:
                return task.state_id
            if col is TaskCol.TITLE:
                return task.title
            if col is TaskCol.ASSIGNEE:
                return task.assignee or ""
            if col is TaskCol.DUE:
                return QDate.fromString(task.due_date, "yyyy-MM-dd") if task.due_date else None
            return None
        if role == Qt.ItemDataRole.CheckStateRole and col is TaskCol.CRITICAL:
            return Qt.CheckState.Checked if task.critical else Qt.CheckState.Unchecked
        if role == SortRole:
            if col is TaskCol.ID:
                return tid
            if col is TaskCol.STATE:
                return st.sort_order if st else 0
            if col is TaskCol.TITLE:
                return task.title.casefold()
            if col is TaskCol.BONES:
                nums = self._bone_numbers(task.bone_ids)
                return number_sort_key(min(nums, key=number_sort_key) if nums else "")
            if col is TaskCol.ASSIGNEE:
                return (task.assignee is None, (task.assignee or "").casefold())
            if col is TaskCol.DUE:
                return (task.due_date is None, task.due_date or "")
            if col is TaskCol.CRITICAL:
                return int(not task.critical)
            if col is TaskCol.UPDATED:
                return task.updated_at
        if role == Qt.ItemDataRole.DecorationRole and col is TaskCol.STATE and st:
            return theme.glyph_icon(st.symbol, st.color, 20)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col is TaskCol.STATE and st:
                return QBrush(QColor(st.color))
            if done and col is not TaskCol.STATE:
                return QBrush(QColor(MUTED_COLOR))
            if col is TaskCol.DUE and doc.is_task_overdue(tid):
                return QBrush(QColor(OVERDUE_COLOR))
        if role == Qt.ItemDataRole.FontRole and col is TaskCol.TITLE and done:
            font = QFont()
            font.setStrikeOut(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole:
            if col is TaskCol.BONES:
                return "\n".join(f"{doc.numbers.get(b, '')} {doc.bone(b).title}"
                                 for b in task.bone_ids if b in doc.tree.bones) or "Unlinked"
            if col is TaskCol.TITLE and task.notes:
                return task.notes
            if col is TaskCol.DUE and doc.is_task_overdue(tid):
                return "Overdue"
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        tid = self._ids[index.row()]
        col = TaskCol(index.column())
        doc = self.doc
        if col is TaskCol.CRITICAL and role == Qt.ItemDataRole.CheckStateRole:
            on = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value, 2, True)
            return doc.update_task(tid, critical=on)
        if role != Qt.ItemDataRole.EditRole:
            return False
        task = doc.tasks[tid]
        if col is TaskCol.STATE:
            return value == task.state_id or doc.set_task_state(tid, int(value))
        if col is TaskCol.TITLE:
            text = str(value or "").strip()
            return text == task.title or doc.update_task(tid, title=text)
        if col is TaskCol.ASSIGNEE:
            name = str(value or "").strip() or None
            return name == task.assignee or doc.update_task(tid, assignee=name)
        if col is TaskCol.DUE:
            due = value.toString("yyyy-MM-dd") if isinstance(value, QDate) and \
                value.isValid() else (value or None)
            return due == task.due_date or doc.update_task(tid, due_date=due)
        return False


class TaskFilterProxy(QSortFilterProxyModel):
    """Text search plus the Tasks window's filters (spec §4.10)."""

    def __init__(self, doc: ProjectDocument, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._text = ""
        self._hide_done = True
        self._assignee: str | None = None  # None = all, "" = unassigned
        self._critical_only = False
        self._overdue_only = False
        self._unlinked_only = False
        self.setSortRole(SortRole)
        self.setDynamicSortFilter(True)

    def _set(self, attr: str, value) -> None:
        if getattr(self, attr) != value:
            self.beginFilterChange()
            setattr(self, attr, value)
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_text(self, text: str) -> None:
        self._set("_text", text.strip().casefold())

    def set_hide_done(self, on: bool) -> None:
        self._set("_hide_done", on)

    def set_assignee(self, value: str | None) -> None:
        self._set("_assignee", None if value is None else value.strip().casefold())

    def set_critical_only(self, on: bool) -> None:
        self._set("_critical_only", on)

    def set_overdue_only(self, on: bool) -> None:
        self._set("_overdue_only", on)

    def set_unlinked_only(self, on: bool) -> None:
        self._set("_unlinked_only", on)

    def filterAcceptsRow(self, row: int, parent: Index) -> bool:
        model = self.sourceModel()
        tid = model.task_id(row)
        task = self.doc.tasks.get(tid)
        if task is None:
            return False
        if self._hide_done and self.doc.is_task_done(tid):
            return False
        if self._assignee is not None and (task.assignee or "").casefold() != self._assignee:
            return False
        if self._critical_only and not task.critical:
            return False
        if self._overdue_only and not self.doc.is_task_overdue(tid):
            return False
        if self._unlinked_only and task.bone_ids:
            return False
        if self._text:
            hay = " ".join((task.title, task.notes, task.assignee or "")).casefold()
            if self._text not in hay:
                return False
        return True

    def lessThan(self, left: Index, right: Index) -> bool:
        a, b = left.data(SortRole), right.data(SortRole)
        if a == b:
            return left.data(TaskIdRole) < right.data(TaskIdRole)
        return a < b

    def visible_task_ids(self) -> list[int]:
        return [self.index(r, 0).data(TaskIdRole) for r in range(self.rowCount())]
