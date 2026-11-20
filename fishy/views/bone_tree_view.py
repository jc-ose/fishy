"""The bone tree-grid (UI-3): keyboard-first outline editing and inline dropdowns.

Keys (when not editing a cell):

    Enter            add a sibling below and rename it
    Tab              add a child and rename it
    Space / F2       rename (edit the title)
    ← / →            outdent / indent
    Ctrl+↑ / Ctrl+↓  move up / down  (Alt+↑ / Alt+↓ also work)
    Shift+Tab        outdent
    Delete           delete
    + / -            expand / collapse   (← / → are taken by outdent / indent)

The view only *requests* actions through signals; the main window performs
them through the document, so every edit is undoable and written through.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from fishy.document import ProjectDocument
from fishy.models.bone_tree_model import BoneIdRole, Col, LevelDerivedRole


def swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(0, 0, size, size, 3, 3)
    p.end()
    return QIcon(pm)


class ChoiceDelegate(QStyledItemDelegate):
    """Dropdown of named, colored choices (levels or statuses) (UI-4).

    Opens its list immediately and commits as soon as an item is picked.
    """

    def __init__(self, doc: ProjectDocument, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.kind = kind  # "level" | "status"

    def _choices(self):
        return self.doc.ordered_levels() if self.kind == "level" else self.doc.ordered_statuses()

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: QModelIndex) -> QWidget:
        combo = QComboBox(parent)
        for choice in self._choices():
            combo.addItem(swatch(choice.color), choice.name, choice.id)
            tip = getattr(choice, "definition", "")
            if tip:
                combo.setItemData(combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        if self.kind == "level" and index.data(LevelDerivedRole):
            combo.setToolTip("This level is rolled up from its causes. Choosing a level "
                             "here pins it (overrides the roll-up).")
        combo.activated.connect(lambda _i, c=combo: self._commit(c))
        QTimer.singleShot(0, combo.showPopup)
        return combo

    def _commit(self, combo: QComboBox) -> None:
        self.commitData.emit(combo)
        self.closeEditor.emit(combo, QStyledItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:
        pos = editor.findData(index.data(Qt.ItemDataRole.EditRole))
        editor.setCurrentIndex(max(pos, 0))

    def setModelData(self, editor: QComboBox, model, index: QModelIndex) -> None:
        if editor.currentData() != index.data(Qt.ItemDataRole.EditRole):
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class BoneTreeView(QTreeView):
    addSiblingRequested = Signal(object)  # current bone id or None
    addChildRequested = Signal(object)
    indentRequested = Signal(int)
    outdentRequested = Signal(int)
    moveUpRequested = Signal(int)
    moveDownRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setAllColumnsShowFocus(True)

    def apply_filter(self, result) -> None:
        """Hide rows outside a `FilterResult` (None shows all) (UI-7).

        Matches show normally; their ancestors stay as muted context, expanded so
        the matches can be seen. The model itself is untouched, so editing,
        drag-and-drop and undo work exactly as without a filter.
        """
        model = self.model()
        if model is None:
            return
        doc = model.doc
        visible = result.visible if result else None
        model.filter_context = result.context if result else frozenset()

        def walk(parent_id, parent_index) -> None:
            for row, bid in enumerate(doc.tree.kids(parent_id)):
                hidden = visible is not None and bid not in visible
                if self.isRowHidden(row, parent_index) != hidden:
                    self.setRowHidden(row, parent_index, hidden)
                index = model.index_for(bid)
                if result and bid in result.context:
                    self.expand(index)
                walk(bid, index)

        walk(None, QModelIndex())
        self.viewport().update()

    def attach(self, doc: ProjectDocument) -> None:
        self.setModel(doc.model)
        self.setItemDelegateForColumn(Col.LEVEL, ChoiceDelegate(doc, "level", self))
        self.setItemDelegateForColumn(Col.STATUS, ChoiceDelegate(doc, "status", self))
        header = self.header()
        header.moveSection(header.visualIndex(Col.NUMBER), 0)  # "#" shown first
        header.moveSection(header.visualIndex(Col.TASKS), header.visualIndex(Col.LOG) + 1)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(Col.TITLE, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(36)
        # Title stretches and gets whatever the fixed columns leave; keep those compact.
        for col, width in ((Col.NUMBER, 56), (Col.LEVEL, 175), (Col.STATUS, 140),
                           (Col.LOG, 40), (Col.TASKS, 52), (Col.UPDATED, 118)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(col, width)
        self.expandAll()

    # ----- helpers -------------------------------------------------------- #

    def current_bone(self) -> int | None:
        idx = self.currentIndex()
        return idx.data(BoneIdRole) if idx.isValid() else None

    def edit_title(self, bone_id: int | None = None) -> None:
        bone_id = self.current_bone() if bone_id is None else bone_id
        if bone_id is None:
            return
        idx = self.model().index_for(bone_id, Col.TITLE)
        self.setCurrentIndex(idx)
        self.edit(idx)

    # ----- keys ----------------------------------------------------------- #

    def event(self, e: QEvent) -> bool:
        # Tab/Backtab normally move focus between widgets before keyPressEvent sees them.
        if (e.type() == QEvent.Type.KeyPress and self.state() != self.State.EditingState
                and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self.state() == self.State.EditingState:
            super().keyPressEvent(e)
            return
        key, mods = e.key(), e.modifiers()
        ctrl_or_alt = bool(mods & (Qt.KeyboardModifier.ControlModifier
                                   | Qt.KeyboardModifier.AltModifier))
        plain = not (mods & ~Qt.KeyboardModifier.KeypadModifier)
        bone = self.current_bone()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain:
            self.addSiblingRequested.emit(bone)
        elif key == Qt.Key.Key_Tab and plain:
            self.addChildRequested.emit(bone)
        elif key == Qt.Key.Key_Backtab and bone is not None:
            self.outdentRequested.emit(bone)
        elif key == Qt.Key.Key_Space and plain:
            self.edit_title()
        elif key == Qt.Key.Key_Left and plain and bone is not None:
            self.outdentRequested.emit(bone)
        elif key == Qt.Key.Key_Right and plain and bone is not None:
            self.indentRequested.emit(bone)
        elif key == Qt.Key.Key_Up and ctrl_or_alt and bone is not None:
            self.moveUpRequested.emit(bone)
        elif key == Qt.Key.Key_Down and ctrl_or_alt and bone is not None:
            self.moveDownRequested.emit(bone)
        elif key == Qt.Key.Key_Delete and bone is not None:
            self.deleteRequested.emit(bone)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and bone is not None:
            self.expand(self.currentIndex().siblingAtColumn(0))
        elif key == Qt.Key.Key_Minus and bone is not None:
            self.collapse(self.currentIndex().siblingAtColumn(0))
        else:
            super().keyPressEvent(e)
            return
        e.accept()
