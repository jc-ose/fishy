"""Details panel for the selected bone (FR-4.1, UI-3).

Shows and edits the title, level (with roll-up and pinning, FR-3.7), status (or a
category's progress summary, FR-3a.6), description, and who/when stamps
(FR-6.4). The evidence log appears here read-only until milestone 5 adds editing.

Every edit goes through the document, so it is undoable and written through.
Text fields commit when they lose focus or after a short pause in typing, so
one burst of typing becomes one undo step.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.models.bone_tree_model import local_time
from fishy.views.bone_tree_view import swatch
from fishy.views.evidence import EvidenceLog

DESCRIPTION_IDLE_MS = 1200


class _FocusOutPlainTextEdit(QPlainTextEdit):
    def __init__(self, on_focus_out, parent=None) -> None:
        super().__init__(parent)
        self._on_focus_out = on_focus_out

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._on_focus_out()


class DetailsPanel(QWidget):
    def __init__(self, parent=None, settings=None) -> None:
        super().__init__(parent)
        self.doc: ProjectDocument | None = None
        self.bone_id: int | None = None

        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)

        self.empty = QLabel("Select a bone in the tree or the diagram to see its details.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        theme.mark(self.empty, muted=True)
        self.empty.setContentsMargins(24, 24, 24, 24)
        self.stack.addWidget(self.empty)

        body = QWidget()
        form_host = QVBoxLayout(body)
        form_host.setContentsMargins(12, 10, 12, 10)

        self.heading = QLabel()
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.kind_label = QLabel()
        theme.mark(self.kind_label, muted=True)
        head_row = QHBoxLayout()
        head_row.addWidget(self.heading)
        head_row.addStretch(1)
        head_row.addWidget(self.kind_label)
        form_host.addLayout(head_row)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.title = QLineEdit()
        self.title.editingFinished.connect(self._commit_title)
        form.addRow("Title", self.title)

        self.level = QComboBox()
        self.level.activated.connect(self._level_chosen)
        self.pin = QCheckBox("Pin")
        self.pin.setToolTip("Override the rolled-up level with this bone's own level")
        self.pin.toggled.connect(self._pin_toggled)
        level_row = QHBoxLayout()
        level_row.addWidget(self.level, 1)
        level_row.addWidget(self.pin)
        form.addRow("Level", level_row)
        self.level_hint = QLabel()
        self.level_hint.setWordWrap(True)
        theme.mark(self.level_hint, muted=True)
        form.addRow("", self.level_hint)

        self.status = QComboBox()
        self.status.activated.connect(self._status_chosen)
        self.summary = QLabel()
        self.status_stack = QStackedWidget()
        self.status_stack.addWidget(self.status)
        self.status_stack.addWidget(self.summary)
        self.status_stack.setSizePolicy(self.status.sizePolicy())
        self.status_stack.setFixedHeight(self.status.sizeHint().height())
        form.addRow("Status", self.status_stack)
        form_host.addLayout(form)

        form_host.addWidget(QLabel("Description"))
        self.description = _FocusOutPlainTextEdit(self._commit_description)
        self.description.setPlaceholderText("What is this cause, and why is it on the diagram? "
                                            "(Markdown is fine.)")
        # Starts at about two lines and grows with its content (up to ~8 lines).
        self.description.document().documentLayout().documentSizeChanged.connect(
            self._fit_description)
        self._fit_description()
        self._desc_timer = QTimer(self, singleShot=True, interval=DESCRIPTION_IDLE_MS)
        self._desc_timer.timeout.connect(self._commit_description)
        self.description.textChanged.connect(self._desc_timer.start)
        form_host.addWidget(self.description)

        self.evidence = EvidenceLog(settings)
        form_host.addWidget(self.evidence)
        self.category_note = QLabel("Categories don't hold evidence; add it to one of their "
                                    "causes.")
        self.category_note.setWordWrap(True)
        theme.mark(self.category_note, muted=True)
        form_host.addWidget(self.category_note)
        form_host.addStretch(1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        theme.mark(line, separator=True)
        form_host.addWidget(line)
        self.stamps = QLabel()
        self.stamps.setWordWrap(True)
        theme.mark(self.stamps, muted=True, small=True)
        form_host.addWidget(self.stamps)

        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        self.setMinimumWidth(300)

    # ----- wiring --------------------------------------------------------- #

    def set_document(self, doc: ProjectDocument | None) -> None:
        self.doc = doc
        self.bone_id = None
        self.evidence.set_document(doc)
        if doc is not None:
            doc.changed.connect(self.refresh)
            doc.choicesChanged.connect(self.refresh)
        self.refresh()

    def show_bone(self, bone_id: int | None) -> None:
        if bone_id == self.bone_id:
            return
        self._flush()
        self.bone_id = bone_id
        self.refresh(force=True)

    def reveal_entry(self, entry_id: int) -> None:
        """Expand an entry's card and scroll it into view (used by the timeline)."""
        self.evidence.collapsed.discard(entry_id)
        self.evidence.refresh()
        card = self.evidence.card_for(entry_id)
        if card is not None:
            theme.mark(card, highlight=True)
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(card, 0, 40))

    def _fit_description(self, *_args) -> None:
        line = self.description.fontMetrics().lineSpacing()
        lines = max(2, min(8, int(self.description.document().size().height())))
        frame = 2 * self.description.frameWidth() + 10
        self.description.setFixedHeight(lines * line + frame)

    def focus_title(self) -> None:
        self.title.setFocus()
        self.title.selectAll()

    # ----- display -------------------------------------------------------- #

    def refresh(self, force: bool = False) -> None:
        doc = self.doc
        if doc is None or self.bone_id is None or self.bone_id not in doc.tree.bones:
            self.bone_id = None
            self.stack.setCurrentWidget(self.empty)
            if self.evidence.bone_id is not None:
                self.evidence.show_bone(None)
            return
        self.stack.setCurrentIndex(1)
        bid = self.bone_id
        bone = doc.bone(bid)
        is_cat = bone.is_category

        self.heading.setText(f"{doc.numbers.get(bid, '')}  {bone.title}")
        self.kind_label.setText("Category" if is_cat else "Cause")
        if force or not self.title.hasFocus():
            self._set_text(self.title, bone.title)
        if force or not self.description.hasFocus():
            self._set_text(self.description, bone.description)

        # Level: show the displayed (effective) level; choosing one sets this bone's own.
        derived = doc.level_is_derived(bid)
        effective = doc.effective_level(bid)
        self._fill(self.level, doc.ordered_levels(), effective.id if effective else None)
        self.level.setEnabled(not is_cat)
        has_kids = bool(doc.tree.kids(bid))
        rollup = doc.project.level_rollup
        self.pin.blockSignals(True)
        self.pin.setChecked(bone.level_pinned)
        self.pin.setVisible(not is_cat and has_kids and rollup)
        self.pin.blockSignals(False)
        if is_cat:
            hint = "Categories show the most severe level among their causes."
        elif derived:
            hint = "Rolled up from its causes. Choosing a level pins it."
        elif bone.level_pinned and has_kids:
            hint = "Pinned: overrides the rolled-up level."
        else:
            hint = effective.definition if effective else ""
        self.level_hint.setText(hint)
        self.level_hint.setVisible(bool(hint))

        if is_cat:
            self.summary.setText(doc.summary(bid).text)
            self.status_stack.setCurrentWidget(self.summary)
        else:
            self._fill(self.status, doc.ordered_statuses(), bone.status_id)
            self.status_stack.setCurrentWidget(self.status)

        self.evidence.setVisible(not is_cat)
        self.category_note.setVisible(is_cat)
        wanted = None if is_cat else bid
        if self.evidence.bone_id != wanted:
            self.evidence.show_bone(wanted)

        users = doc.repo.users()

        def who(uid):
            return users[uid].display_name if uid in users else "unknown"

        self.stamps.setText(
            f"Created {local_time(bone.created_at)} by {who(bone.created_by)}<br>"
            f"Updated {local_time(bone.updated_at)} by {who(bone.updated_by)}<br>"
            f"Bone id {bid}")

    @staticmethod
    def _fill(combo: QComboBox, choices, current_id: int | None) -> None:
        combo.blockSignals(True)
        combo.clear()
        for ch in choices:
            combo.addItem(swatch(ch.color), ch.name, ch.id)
        combo.setCurrentIndex(combo.findData(current_id) if current_id is not None else -1)
        combo.blockSignals(False)

    @staticmethod
    def _set_text(widget, text: str) -> None:
        current = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
        if current != text:
            widget.blockSignals(True)
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(text)
            else:
                widget.setText(text)
            widget.blockSignals(False)

    # ----- edits ---------------------------------------------------------- #

    def _flush(self) -> None:
        """Commit any pending text edits before switching bones."""
        if self.doc and self.bone_id in (self.doc.tree.bones if self.doc else {}):
            self._commit_title()
            self._commit_description()

    def _commit_title(self) -> None:
        if self.doc and self.bone_id in self.doc.tree.bones:
            text = self.title.text().strip()
            if not text:
                self._set_text(self.title, self.doc.bone(self.bone_id).title)
                return
            self.doc.rename_bone(self.bone_id, text)

    def _commit_description(self) -> None:
        self._desc_timer.stop()
        if self.doc and self.bone_id in self.doc.tree.bones:
            text = self.description.toPlainText()
            if text != self.doc.bone(self.bone_id).description:
                self.doc.set_description(self.bone_id, text)

    def _level_chosen(self, _index: int) -> None:
        level_id = self.level.currentData()
        if self.doc and level_id is not None and level_id != self.doc.effective.get(self.bone_id):
            self.doc.set_level(self.bone_id, level_id)

    def _pin_toggled(self, on: bool) -> None:
        if self.doc:
            self.doc.set_pinned(self.bone_id, on)

    def _status_chosen(self, _index: int) -> None:
        status_id = self.status.currentData()
        if not self.doc or status_id is None:
            return
        if status_id != self.doc.bone(self.bone_id).status_id:
            self.doc.set_status(self.bone_id, status_id)
