"""`BoneTreeModel`: the bone hierarchy as a Qt item model (UI-3).

The model is a read-mostly view over `ProjectDocument`'s caches. Edits made
through `setData` are routed back to the document, so they are undoable and
written through like any other change. The document drives the structural
notifications (`begin_insert` / `begin_move` / `begin_remove`) around its
cache reloads, which keeps attached views and selections stable.

Each index's `internalId` is the bone's stable id.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from fishy import theme

if TYPE_CHECKING:
    from fishy.document import ProjectDocument

Index = QModelIndex | QPersistentModelIndex


class Col(IntEnum):
    # Title is column 0 (the tree column, and stable data for Qt's model
    # checks); the editor shows the number first by reordering header sections.
    TITLE = 0
    NUMBER = 1
    LEVEL = 2
    STATUS = 3
    LOG = 4
    UPDATED = 5


HEADERS = {Col.TITLE: "Title", Col.NUMBER: "#", Col.LEVEL: "Level", Col.STATUS: "Status",
           Col.LOG: "Log", Col.UPDATED: "Updated"}

BoneIdRole = Qt.ItemDataRole.UserRole + 1
IsCategoryRole = Qt.ItemDataRole.UserRole + 2
LevelIdRole = Qt.ItemDataRole.UserRole + 3  # effective (displayed) level id
LevelDerivedRole = Qt.ItemDataRole.UserRole + 4
PinnedRole = Qt.ItemDataRole.UserRole + 5
StatusIdRole = Qt.ItemDataRole.UserRole + 6

BONE_MIME = "application/x-fishy-bone"


def local_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    return dt.strftime("%Y-%m-%d %H:%M")


class BoneTreeModel(QAbstractItemModel):
    def __init__(self, doc: ProjectDocument) -> None:
        super().__init__(doc)
        self.doc = doc
        # Set by the UI: asked before a drop converts a bone between category and
        # cause (FR-1a.2). Receives (bone_id, new_kind); returns True to proceed.
        self.confirm_kind_change: Callable[[int, str], bool] | None = None

    # ----- structure ----------------------------------------------------- #

    def _bone_id(self, index: Index) -> int | None:
        return int(index.internalId()) if index.isValid() else None

    def index_for(self, bone_id: int | None, column: int = 0) -> QModelIndex:
        if bone_id is None or bone_id not in self.doc.tree.bones:
            return QModelIndex()
        b = self.doc.tree.bones[bone_id]
        return self.createIndex(b.position, column, bone_id)

    def index(self, row: int, column: int, parent: Index = QModelIndex()) -> QModelIndex:
        if column < 0 or column >= len(Col):
            return QModelIndex()
        kids = self.doc.tree.kids(self._bone_id(parent))
        if 0 <= row < len(kids):
            return self.createIndex(row, column, kids[row])
        return QModelIndex()

    def parent(self, index: Index = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        bone = self.doc.tree.bones.get(int(index.internalId()))
        if bone is None or bone.parent_id is None:
            return QModelIndex()
        return self.index_for(bone.parent_id)

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self.doc.tree.kids(self._bone_id(parent)))

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return len(Col)

    def hasChildren(self, parent: Index = QModelIndex()) -> bool:
        return self.rowCount(parent) > 0

    # ----- data ---------------------------------------------------------- #

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[Col(section)]
        return None

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        doc = self.doc
        bid = int(index.internalId())
        bone = doc.tree.bones.get(bid)
        if bone is None:
            return None
        col = Col(index.column())

        if role == BoneIdRole:
            return bid
        if role == IsCategoryRole:
            return bone.is_category
        if role == LevelIdRole:
            return doc.effective.get(bid)
        if role == LevelDerivedRole:
            return doc.level_is_derived(bid)
        if role == PinnedRole:
            return bone.level_pinned
        if role == StatusIdRole:
            return bone.status_id

        if role == Qt.ItemDataRole.FontRole and bone.is_category:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.BackgroundRole and bone.is_category:
            return QColor(*theme.current().category_tint)  # FR-1a.5: group headers

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col is Col.NUMBER:
                return doc.numbers.get(bid, "")
            if col is Col.TITLE:
                return bone.title
            if col is Col.LEVEL:
                level = doc.effective_level(bid)
                if level is None:
                    return "" if role == Qt.ItemDataRole.DisplayRole else None
                if role == Qt.ItemDataRole.EditRole:
                    return level.id
                if bone.level_pinned:
                    return f"{level.name} (pinned)"
                if doc.level_is_derived(bid):
                    return f"{level.name} (auto)"
                return level.name
            if col is Col.STATUS:
                if bone.is_category:
                    return doc.summary(bid).text if role == Qt.ItemDataRole.DisplayRole else None
                st = doc.statuses.get(bone.status_id)  # type: ignore[arg-type]
                if role == Qt.ItemDataRole.EditRole:
                    return bone.status_id
                return st.name if st else ""
            if col is Col.LOG:
                return "" if bone.is_category else doc.log_counts.get(bid, 0)
            if col is Col.UPDATED:
                return local_time(bone.updated_at)

        if role == Qt.ItemDataRole.DecorationRole:
            if col is Col.LEVEL:
                level = doc.effective_level(bid)
                return QColor(level.color) if level else None
            if col is Col.STATUS and not bone.is_category:
                st = doc.statuses.get(bone.status_id)  # type: ignore[arg-type]
                return QColor(st.color) if st else None

        if role == Qt.ItemDataRole.ForegroundRole and col in (Col.LEVEL, Col.UPDATED):
            if col is Col.UPDATED or doc.level_is_derived(bid):
                return QColor(theme.current().muted)

        if role == Qt.ItemDataRole.ToolTipRole and col is Col.LEVEL:
            level = doc.effective_level(bid)
            if level:
                how = ("Rolled up from its causes" if doc.level_is_derived(bid)
                       else "Pinned: overrides roll-up" if bone.level_pinned else "Set directly")
                return f"{level.name}: {level.definition}\n{how}"
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled  # drop on empty space: new category
        base = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        bone = self.doc.tree.bones[int(index.internalId())]
        col = Col(index.column())
        if col is Col.TITLE:
            base |= Qt.ItemFlag.ItemIsEditable
        elif col is Col.LEVEL and not bone.is_category:
            base |= Qt.ItemFlag.ItemIsEditable  # editing a rolled-up level pins it
        elif col is Col.STATUS and not bone.is_category:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        bid = int(index.internalId())
        col = Col(index.column())
        if col is Col.TITLE:
            return self.doc.rename_bone(bid, str(value))
        if col is Col.LEVEL and value in self.doc.levels:
            return self.doc.set_level(bid, int(value))
        if col is Col.STATUS and value in self.doc.statuses:
            return self.doc.set_status(bid, int(value))
        return False

    # ----- drag and drop (FR-1.1) ---------------------------------------- #

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [BONE_MIME]

    def mimeData(self, indexes) -> QMimeData:
        data = QMimeData()
        ids = {int(i.internalId()) for i in indexes if i.isValid()}
        if len(ids) == 1:
            data.setData(BONE_MIME, str(ids.pop()).encode())
        return data

    @staticmethod
    def _dragged_bone(data: QMimeData) -> int | None:
        if not data.hasFormat(BONE_MIME):
            return None
        try:
            return int(bytes(data.data(BONE_MIME)).decode())
        except ValueError:
            return None

    def canDropMimeData(self, data: QMimeData, action: Qt.DropAction, row: int, column: int,
                        parent: Index) -> bool:
        bone = self._dragged_bone(data)
        if bone is None or bone not in self.doc.tree.bones:
            return False
        target = self._bone_id(parent)
        return target is None or (target != bone and target not in set(self.doc.tree.walk(bone)))

    def drop_target(self, row: int, parent: Index) -> tuple[int | None, int]:
        """(new parent id, index) for a drop at `row` under `parent` (-1 = onto parent)."""
        new_parent = self._bone_id(parent)
        return new_parent, (self.rowCount(parent.siblingAtColumn(0) if parent.isValid()
                                          else QModelIndex()) if row < 0 else row)

    def dropMimeData(self, data: QMimeData, action: Qt.DropAction, row: int, column: int,
                     parent: Index) -> bool:
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        bone_id = self._dragged_bone(data)
        new_parent, index = self.drop_target(row, parent)
        b = self.doc.bone(bone_id)
        if b.parent_id == new_parent and index > b.position:
            index -= 1  # Qt counts rows including the one being moved
        kind = self.doc.kind_change(bone_id, new_parent)
        if kind and self.confirm_kind_change and not self.confirm_kind_change(bone_id, kind):
            return False
        self.doc.move_bone(bone_id, new_parent, index, "Drag")
        # The document already moved the row; returning False stops the view from
        # also trying to remove the source row.
        return False

    # ----- notifications driven by the document -------------------------- #

    def begin_insert(self, parent_id: int | None, row: int) -> None:
        self.beginInsertRows(self.index_for(parent_id), row, row)

    def end_insert(self) -> None:
        self.endInsertRows()

    def begin_remove(self, parent_id: int | None, row: int) -> None:
        self.beginRemoveRows(self.index_for(parent_id), row, row)

    def end_remove(self) -> None:
        self.endRemoveRows()

    def begin_move(self, old_parent: int | None, old_row: int,
                   new_parent: int | None, new_row: int) -> bool:
        """Returns False for a no-op move (nothing to notify)."""
        dest = new_row
        if old_parent == new_parent:
            if new_row == old_row:
                return False
            if new_row > old_row:
                dest = new_row + 1  # Qt counts the destination before removal
        return self.beginMoveRows(self.index_for(old_parent), old_row, old_row,
                                  self.index_for(new_parent), dest)

    def end_move(self) -> None:
        self.endMoveRows()

    def refresh_derived(self) -> None:
        """Numbers, rolled-up levels and summaries can change anywhere: refresh all cells."""
        last = len(Col) - 1

        def walk(parent_id: int | None, parent_index: QModelIndex) -> None:
            kids = self.doc.tree.kids(parent_id)
            if not kids:
                return
            self.dataChanged.emit(self.index(0, 0, parent_index),
                                  self.index(len(kids) - 1, last, parent_index))
            for row, kid in enumerate(kids):
                walk(kid, self.index(row, 0, parent_index))

        walk(None, QModelIndex())
