"""Levels & statuses editor (FR-3.1 – 3.6, FR-3a.1 – 3a.3).

Every change applies immediately through the document (so it's undoable and
written through), and the diagram and legend update live behind the dialog.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.domain import StatusRole
from fishy.views.bone_tree_view import swatch
from fishy.views.details_panel import DESCRIPTION_IDLE_MS, _FocusOutPlainTextEdit

LEVEL_TEMPLATE_KEY = "templates/levels"

ROLE_LABELS = {
    StatusRole.NOT_STARTED: "Not started",
    StatusRole.ACTIVE: "Active (in progress)",
    StatusRole.BLOCKED: "Blocked",
    StatusRole.REVIEW: "In review",
    StatusRole.DONE: "Done (counts as complete)",
}


class _ChoiceTab(QWidget):
    """List + form editor for one kind of choice ("level" or "status")."""

    def __init__(self, doc: ProjectDocument, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.kind = kind
        self.is_level = kind == "level"

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        hint = QLabel("Top = most severe. Order drives level roll-up." if self.is_level
                      else "Order sets how statuses are listed.")
        theme.mark(hint, muted=True)
        hint.setWordWrap(True)
        left.addWidget(hint)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.del_btn = QPushButton("Delete…")
        self.up_btn = QPushButton("▲")
        self.down_btn = QPushButton("▼")
        self.up_btn.setToolTip("Move up" + (" (more severe)" if self.is_level else ""))
        self.down_btn.setToolTip("Move down" + (" (less severe)" if self.is_level else ""))
        for b in (self.add_btn, self.del_btn, self.up_btn, self.down_btn):
            buttons.addWidget(b)
        left.addLayout(buttons)
        layout.addLayout(left, 1)

        form_host = QWidget()
        form = QFormLayout(form_host)
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._name_edited)
        form.addRow("Name", self.name)
        self.color_btn = QPushButton()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("Color", self.color_btn)
        if self.is_level:
            self.definition = _FocusOutPlainTextEdit(self.flush)
            self.definition.setPlaceholderText("Shown in the legend and as a tooltip")
            self.definition.setMaximumHeight(90)
            self._def_dirty = False
            self._def_timer = QTimer(self, singleShot=True, interval=DESCRIPTION_IDLE_MS)
            self._def_timer.timeout.connect(self.flush)
            self.definition.textChanged.connect(self._definition_typed)
            form.addRow("Definition", self.definition)
            self.eliminated = QCheckBox("Means “eliminated” (drawn dimmed and struck through)")
            self.eliminated.toggled.connect(self._eliminated_toggled)
            form.addRow("", self.eliminated)
        else:
            self.role = QComboBox()
            for role, label in ROLE_LABELS.items():
                self.role.addItem(label, role)
            self.role.setToolTip("What this status means for progress summaries")
            self.role.activated.connect(self._role_chosen)
            form.addRow("Meaning", self.role)
        self.default_box = QCheckBox("Default for new causes")
        self.default_box.toggled.connect(self._default_toggled)
        form.addRow("", self.default_box)
        self.usage = QLabel()
        theme.mark(self.usage, muted=True)
        form.addRow("", self.usage)
        layout.addWidget(form_host, 1)

        self.add_btn.clicked.connect(self._add)
        self.del_btn.clicked.connect(self._delete)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(+1))
        doc.choicesChanged.connect(self.reload)
        self.reload()

    # ----- data helpers --------------------------------------------------- #

    def _choices(self):
        return self.doc.ordered_levels() if self.is_level else self.doc.ordered_statuses()

    def _current(self):
        choices = self._choices()
        row = self.list.currentRow()
        return choices[row] if 0 <= row < len(choices) else None

    def _usage_count(self, choice_id: int) -> int:
        attr = "level_id" if self.is_level else "status_id"
        return sum(1 for b in self.doc.tree.bones.values() if getattr(b, attr) == choice_id)

    # ----- display -------------------------------------------------------- #

    def reload(self) -> None:
        current = self._current()
        keep_id = current.id if current else None
        self.list.blockSignals(True)
        self.list.clear()
        for ch in self._choices():
            text = ch.name + ("   (default)" if ch.is_default else "")
            item = QListWidgetItem(swatch(ch.color, 14), text)
            item.setData(Qt.ItemDataRole.UserRole, ch.id)
            self.list.addItem(item)
        ids = [c.id for c in self._choices()]
        self.list.setCurrentRow(ids.index(keep_id) if keep_id in ids else 0)
        self.list.blockSignals(False)
        self._show_current()

    def _show_current(self, *_args) -> None:
        self.flush()  # don't lose a definition being typed when switching rows
        ch = self._current()
        for w in (self.name, self.color_btn, self.default_box, self.del_btn):
            w.setEnabled(ch is not None)
        if ch is None:
            return
        row = self.list.currentRow()
        self.up_btn.setEnabled(row > 0)
        self.down_btn.setEnabled(row < self.list.count() - 1)
        self._shown_id = ch.id
        self.name.setText(ch.name)
        self.color_btn.setText(ch.color)
        self.color_btn.setIcon(swatch(ch.color, 14))
        self.default_box.blockSignals(True)
        self.default_box.setChecked(ch.is_default)
        self.default_box.setEnabled(not ch.is_default)  # pick another to change the default
        self.default_box.blockSignals(False)
        if self.is_level:
            self.definition.blockSignals(True)
            self.definition.setPlainText(ch.definition)
            self.definition.blockSignals(False)
            self._def_dirty = False
            self.eliminated.blockSignals(True)
            self.eliminated.setChecked(ch.eliminated)
            self.eliminated.blockSignals(False)
        else:
            self.role.setCurrentIndex(self.role.findData(ch.role))
        n = self._usage_count(ch.id)
        self.usage.setText(f"Used by {n} cause{'s' if n != 1 else ''}"
                           + (" (directly)" if self.is_level else ""))

    # ----- edits ---------------------------------------------------------- #

    def _update(self, **fields) -> None:
        ch = self._current()
        if ch is None:
            return
        if self.is_level:
            self.doc.update_level(ch.id, **fields)
        else:
            self.doc.update_status(ch.id, **fields)

    def _name_edited(self) -> None:
        ch = self._current()
        text = self.name.text().strip()
        if ch and text and text != ch.name:
            self._update(name=text)

    def _pick_color(self) -> None:
        ch = self._current()
        if ch is None:
            return
        color = QColorDialog.getColor(QColor(ch.color), self, f"Color for {ch.name}")
        if color.isValid() and color.name() != ch.color:
            self._update(color=color.name())

    def _definition_edited(self) -> None:
        # Save to the level whose definition is on screen, which may no longer be the
        # current row (this runs after the list selection has already moved).
        level = self.doc.levels.get(getattr(self, "_shown_id", None))
        text = self.definition.toPlainText()
        self._def_dirty = False
        if level and text != level.definition:
            self.doc.update_level(level.id, definition=text)

    def _definition_typed(self) -> None:
        self._def_dirty = True
        self._def_timer.start()

    def flush(self) -> None:
        """Save a typed-but-unsaved definition (pause, focus-out, or dialog close)."""
        if self.is_level and self._def_dirty:
            self._def_timer.stop()
            self._definition_edited()

    def _eliminated_toggled(self, on: bool) -> None:
        self._update(eliminated=on)

    def _role_chosen(self, _index: int) -> None:
        ch = self._current()
        role = self.role.currentData()
        if ch and role != ch.role:
            self._update(role=role)

    def _default_toggled(self, on: bool) -> None:
        ch = self._current()
        if ch and on:
            if self.is_level:
                self.doc.set_default_level(ch.id)
            else:
                self.doc.set_default_status(ch.id)

    def _add(self) -> None:
        label = "level" if self.is_level else "status"
        name, ok = QInputDialog.getText(self, f"Add {label}", f"Name of the new {label}:")
        if not ok or not name.strip():
            return
        row = self.list.currentRow()
        index = row + 1 if row >= 0 else None
        if self.is_level:
            self.doc.add_level(name.strip(), "#607d8b", index=index)
        else:
            self.doc.add_status(name.strip(), "#607d8b", StatusRole.ACTIVE, index=index)
        self.list.setCurrentRow(index if index is not None else self.list.count() - 1)

    def _delete(self) -> None:
        ch = self._current()
        if ch is None:
            return
        others = [c for c in self._choices() if c.id != ch.id]
        if not others:
            QMessageBox.information(self, "Can't delete", "At least one must remain.")
            return
        n = self._usage_count(ch.id)
        target = others[0]
        if n:  # FR-3.4 / FR-3a.3: reassign bones that use it
            names = [c.name for c in others]
            choice, ok = QInputDialog.getItem(
                self, "Reassign causes",
                f"{n} cause{'s use' if n != 1 else ' uses'} “{ch.name}”. Move them to:",
                names, 0, False)
            if not ok:
                return
            target = others[names.index(choice)]
        elif QMessageBox.question(self, "Delete", f"Delete “{ch.name}”?") != \
                QMessageBox.StandardButton.Yes:
            return
        if self.is_level:
            self.doc.delete_level(ch.id, target.id)
        else:
            self.doc.delete_status(ch.id, target.id)

    def _move(self, delta: int) -> None:
        ch = self._current()
        if ch is None:
            return
        row = self.list.currentRow() + delta
        if self.is_level:
            self.doc.move_level(ch.id, row)
        else:
            self.doc.move_status(ch.id, row)
        self.list.setCurrentRow(row)


class ChoicesDialog(QDialog):
    def __init__(self, doc: ProjectDocument, settings: QSettings | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.settings = settings
        self.setWindowTitle("Levels & statuses")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.levels_tab = _ChoiceTab(doc, "level")
        self.status_tab = _ChoiceTab(doc, "status")
        self.tabs.addTab(self.levels_tab, "Levels")
        self.tabs.addTab(self.status_tab, "Statuses")
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.template_btn = QPushButton("Use these levels for new projects")
        self.template_btn.setToolTip("Save this level set as the starting point for projects "
                                     "you create from now on")
        self.template_btn.clicked.connect(self.save_template)
        self.template_btn.setEnabled(settings is not None)
        bottom.addWidget(self.template_btn)
        bottom.addStretch(1)
        note = QLabel("Changes apply immediately and can be undone (Ctrl+Z).")
        theme.mark(note, muted=True)
        bottom.addWidget(note)
        # The main window's undo shortcuts don't reach a modal dialog; add our own.
        for seq, fn in ((QKeySequence.StandardKey.Undo, doc.undo_stack.undo),
                        (QKeySequence.StandardKey.Redo, doc.undo_stack.redo),
                        (QKeySequence("Ctrl+Y"), doc.undo_stack.redo)):
            QShortcut(seq, self, activated=fn)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    def save_template(self) -> None:
        if self.settings is None:
            return
        self.levels_tab.flush()
        self.settings.setValue(LEVEL_TEMPLATE_KEY, json.dumps(self.doc.repo.level_template()))
        self.template_btn.setText("Saved ✓  — new projects will start with these levels")

    def done(self, result: int) -> None:
        self.levels_tab.flush()
        super().done(result)


def saved_level_template(settings: QSettings) -> list[dict] | None:
    raw = settings.value(LEVEL_TEMPLATE_KEY)
    if not raw:
        return None
    try:
        levels = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return levels if isinstance(levels, list) and levels else None
