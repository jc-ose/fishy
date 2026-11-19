"""Filter controls (UI-7): text, levels and statuses, in a drop-down from the toolbar.

The panel lives in a menu (via QWidgetAction), so ticking several boxes doesn't
close it. It only describes the filter; the main window applies it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.filtering import BoneFilter
from fishy.views.bone_tree_view import swatch


class FilterPanel(QWidget):
    filterChanged = Signal(object)  # BoneFilter

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc: ProjectDocument | None = None
        self.level_boxes: dict[int, QCheckBox] = {}
        self.status_boxes: dict[int, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Title, number or description contains…")
        self.text.setClearButtonEnabled(True)
        layout.addWidget(self.text)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(18)
        layout.addLayout(self.grid)

        hint = theme.mark(QLabel("Within a list, any ticked item matches. Across text, levels "
                                 "and statuses, all must match.\nMatches show normally, "
                                 "their parents in grey; the rest are hidden in the grid and "
                                 "faded on the diagram.\nExports are not affected."),
                          muted=True, small=True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        self.clear_btn = QPushButton("Clear filter")
        self.clear_btn.clicked.connect(self.clear)
        row.addWidget(self.clear_btn)
        layout.addLayout(row)

        self._timer = QTimer(self, singleShot=True, interval=150)  # typing settles first
        self._timer.timeout.connect(self._emit)
        self.text.textChanged.connect(self._timer.start)

    # ----- choices ---------------------------------------------------------- #

    def set_document(self, doc: ProjectDocument | None) -> None:
        self.doc = doc
        self.text.blockSignals(True)
        self.text.clear()
        self.text.blockSignals(False)
        self.rebuild(keep=False)

    def rebuild(self, keep: bool = True) -> None:
        """Recreate the checkboxes after levels or statuses change, keeping ticks."""
        old = self.current() if keep else BoneFilter()
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.level_boxes.clear()
        self.status_boxes.clear()
        if self.doc is None:
            return
        for col, (title, choices, boxes, chosen) in enumerate((
                ("Levels", self.doc.ordered_levels(), self.level_boxes, old.level_ids),
                ("Statuses", self.doc.ordered_statuses(), self.status_boxes, old.status_ids))):
            head = QLabel(title)
            head.setStyleSheet("font-weight: 600;")
            self.grid.addWidget(head, 0, col)
            for row, ch in enumerate(choices, start=1):
                box = QCheckBox(ch.name)
                box.setIcon(swatch(ch.color))
                box.setChecked(ch.id in chosen)
                box.toggled.connect(self._emit)
                self.grid.addWidget(box, row, col, Qt.AlignmentFlag.AlignTop)
                boxes[ch.id] = box
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    # ----- state ------------------------------------------------------------ #

    def current(self) -> BoneFilter:
        return BoneFilter(
            text=self.text.text(),
            level_ids=frozenset(i for i, b in self.level_boxes.items() if b.isChecked()),
            status_ids=frozenset(i for i, b in self.status_boxes.items() if b.isChecked()))

    def set_filter(self, f: BoneFilter) -> None:
        for boxes, chosen in ((self.level_boxes, f.level_ids), (self.status_boxes, f.status_ids)):
            for i, b in boxes.items():
                b.blockSignals(True)
                b.setChecked(i in chosen)
                b.blockSignals(False)
        self.text.blockSignals(True)
        self.text.setText(f.text)
        self.text.blockSignals(False)
        self._emit()

    def clear(self) -> None:
        self.set_filter(BoneFilter())

    def _emit(self, *_args) -> None:
        self._timer.stop()
        self.filterChanged.emit(self.current())
