"""Evidence log UI (FR-5.x): entry cards, the entry editor, image viewer, timeline.

* `EvidenceLog` — the list of a cause's entries inside the details panel. Each
  entry is a card with its rendered markdown. Cards can be collapsed to one
  line (FR-5.1b); the list can be in manual order (▲/▼ to reorder) or by
  date (FR-5.1a).
* `EntryEditor` — add/edit an entry: markdown on the left, live preview on the
  right (FR-5.5). Paste or drop images, or insert them from disk (FR-5.3).
* `ImageViewer` — full-size view of an attached image, with Save As.
* `TimelineDialog` — every entry in the project in date order (FR-5.6).

Images added in the editor are stored right away but don't belong to an entry
until it's saved. Cancelling discards them, so nothing is left behind.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.domain import LogEntry
from fishy.markdown_render import summary_line
from fishy.models.bone_tree_model import local_time
from fishy.views.markdown_view import MarkdownView

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
ORDER_KEY = "evidence/order"


def bone_label_fn(doc: ProjectDocument):
    def label(bone_id: int) -> str | None:
        if bone_id not in doc.tree.bones:
            return None
        return f"{doc.numbers.get(bone_id, '')} {doc.bone(bone_id).title}".strip()

    return label


def attachment_loader(doc: ProjectDocument):
    def load(att_id: int) -> bytes | None:
        try:
            return doc.repo.attachment_data(att_id)
        except KeyError:
            return None

    return load


def make_markdown_view(doc: ProjectDocument, auto_height: bool = False, parent=None
                       ) -> MarkdownView:
    view = MarkdownView(parent, auto_height=auto_height)
    view.load_attachment = attachment_loader(doc)
    view.bone_label = bone_label_fn(doc)
    view.imageClicked.connect(lambda att: ImageViewer(doc, att, view.window()).exec())
    return view


# =========================================================================== #
# Editor
# =========================================================================== #


class _ImageAwareEdit(QPlainTextEdit):
    """Markdown source editor that turns pasted/dropped images into attachments."""

    def __init__(self, add_image, parent=None) -> None:
        super().__init__(parent)
        self._add_image = add_image  # (bytes, mime, filename) -> attachment id | None

    @staticmethod
    def _image_files(source) -> list[Path]:
        if not source.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in source.urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES]

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasImage() or bool(self._image_files(source)) or \
            super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        files = self._image_files(source)
        if files:
            for f in files:
                self.insert_image_file(f)
            return
        if source.hasImage():
            image = QImage(source.imageData())
            if not image.isNull():
                self.insert_image_bytes(png_bytes(image), "image/png", "pasted image")
                return
        super().insertFromMimeData(source)

    def insert_image_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError as exc:
            QMessageBox.warning(self, "Can't read image", str(exc))
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.insert_image_bytes(data, mime, path.name)

    def insert_image_bytes(self, data: bytes, mime: str, name: str) -> None:
        if len(data) > MAX_IMAGE_BYTES:
            QMessageBox.warning(self, "Image too large",
                                f"Images up to {MAX_IMAGE_BYTES // 2**20} MB can be attached.")
            return
        att_id = self._add_image(data, mime, name)
        if att_id is None:
            return
        caption = Path(name).stem if "." in name else name
        cursor = self.textCursor()
        prefix = "" if cursor.atBlockStart() else "\n"
        cursor.insertText(f"{prefix}![{caption}](attachment:{att_id})\n")
        self.setTextCursor(cursor)


def png_bytes(image: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


class EntryEditor(QDialog):
    """Add or edit one evidence entry, with live preview (FR-5.5)."""

    PREVIEW_DELAY_MS = 250

    def __init__(self, doc: ProjectDocument, bone_id: int, entry: LogEntry | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.bone_id = bone_id
        self.entry = entry
        self.new_attachments: list[int] = []
        bone = doc.bone(bone_id)
        self.setWindowTitle(("Edit entry" if entry else "New entry")
                            + f" — {doc.numbers.get(bone_id, '')} {bone.title}")
        self.resize(1000, 640)

        layout = QVBoxLayout(self)
        self.title = QLineEdit(entry.title or "" if entry else "")
        self.title.setPlaceholderText("Title (optional), e.g. “Training records”")
        layout.addWidget(self.title)

        bar = QToolBar()
        for label, tip, fn in (
                ("B", "Bold (Ctrl+B)", lambda: self._wrap("**")),
                ("I", "Italic (Ctrl+I)", lambda: self._wrap("*")),
                ("</>", "Inline code", lambda: self._wrap("`")),
                ("• List", "Bulleted list", lambda: self._prefix_lines("- ")),
                ("☐ Task", "Task list", lambda: self._prefix_lines("- [ ] ")),
                ("{ } Code", "Code block", self._code_block),
        ):
            act = bar.addAction(label, fn)
            act.setToolTip(tip)
        bar.addSeparator()
        bar.addAction("Insert image…", self._pick_images).setToolTip(
            "Attach images from disk (you can also paste or drag them in)")
        bar.addAction("Link bone…", self._link_bone).setToolTip(
            "Insert a link to another bone; it always shows that bone's current number")
        layout.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.source = _ImageAwareEdit(self._add_image)
        self.source.setPlaceholderText(
            "Write findings, data or analysis in Markdown.\n\n"
            "**bold**, *italic*, `code`, tables, - [ ] task lists, ```python fenced code```.\n"
            "Paste or drag images straight in.")
        self.source.setPlainText(entry.body_md if entry else "")
        font = self.source.font()
        font.setFamilies(["Consolas", "Cascadia Mono", "monospace"])
        self.source.setFont(font)
        self.preview = make_markdown_view(doc, parent=self)
        split.addWidget(self.source)
        split.addWidget(self.preview)
        split.setSizes([500, 500])
        layout.addWidget(split, 1)

        hint = QLabel("Ctrl+Enter saves. Escape cancels; images added here are discarded "
                      "if you cancel.")
        theme.mark(hint, muted=True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.accept)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=lambda: self._wrap("**"))
        QShortcut(QKeySequence("Ctrl+I"), self, activated=lambda: self._wrap("*"))

        self._timer = QTimer(self, singleShot=True, interval=self.PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._update_preview)
        self.source.textChanged.connect(self._timer.start)
        self._update_preview()
        self.source.setFocus()

    # ----- content -------------------------------------------------------- #

    def body(self) -> str:
        return self.source.toPlainText()

    def entry_title(self) -> str | None:
        return self.title.text().strip() or None

    def _update_preview(self) -> None:
        self.preview.set_markdown(self.body() or "*Nothing written yet.*")

    def _add_image(self, data: bytes, mime: str, name: str) -> int | None:
        att = self.doc.repo.add_attachment(None, data, mime, name)
        if att not in self.new_attachments:
            self.new_attachments.append(att)
        return att

    def _pick_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Insert images", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        for f in files:
            self.source.insert_image_file(Path(f))

    def _link_bone(self) -> None:
        label = bone_label_fn(self.doc)
        ids = [b for b in self.doc.tree.walk() if b != self.bone_id]
        if not ids:
            return
        names = [label(b) for b in ids]
        choice, ok = QInputDialog.getItem(self, "Link bone", "Bone:", names, 0, False)
        if ok:
            self.source.insertPlainText(f"[[bone:{ids[names.index(choice)]}]]")
            self.source.setFocus()

    def _wrap(self, marker: str) -> None:
        cursor = self.source.textCursor()
        text = cursor.selectedText()
        cursor.insertText(f"{marker}{text}{marker}")
        if not text:
            cursor.movePosition(QTextCursor.MoveOperation.Left, n=len(marker))
            self.source.setTextCursor(cursor)
        self.source.setFocus()

    def _prefix_lines(self, prefix: str) -> None:
        cursor = self.source.textCursor()
        start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        cursor.setPosition(start)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.beginEditBlock()
        while True:
            cursor.insertText(prefix)
            if not cursor.movePosition(QTextCursor.MoveOperation.NextBlock) or \
                    cursor.position() > end + len(prefix):
                break
            end += len(prefix)
        cursor.endEditBlock()
        self.source.setFocus()

    def _code_block(self) -> None:
        cursor = self.source.textCursor()
        text = cursor.selectedText().replace(" ", "\n")
        cursor.insertText(f"\n```\n{text}\n```\n")
        self.source.setFocus()

    # ----- finish --------------------------------------------------------- #

    def accept(self) -> None:
        if not self.body().strip() and not self.entry_title():
            QMessageBox.information(self, "Empty entry", "Write something before saving, "
                                    "or press Cancel.")
            return
        super().accept()

    def reject(self) -> None:
        if self.new_attachments:
            self.doc.repo.discard_unowned_attachments(self.new_attachments)
            self.new_attachments.clear()
        super().reject()


# =========================================================================== #
# Image viewer
# =========================================================================== #


class ImageViewer(QDialog):
    def __init__(self, doc: ProjectDocument, att_id: int, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        try:
            self.att = doc.repo.attachment(att_id)
            self.data = doc.repo.attachment_data(att_id)
        except KeyError:
            self.att, self.data = None, b""
        self.setWindowTitle(self.att.filename or "Image" if self.att else "Image not found")
        self.pixmap = QPixmap()
        self.pixmap.loadFromData(self.data)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.label)
        scroll.setWidgetResizable(True)
        self.scroll = scroll

        self.fit = QPushButton("Actual size")
        self.fit.setCheckable(True)
        self.fit.toggled.connect(self._show)
        save = QPushButton("Save image as…")
        save.clicked.connect(self._save)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        row = QHBoxLayout()
        info = QLabel(f"{self.pixmap.width()} × {self.pixmap.height()} px")
        theme.mark(info, muted=True)
        row.addWidget(info)
        row.addStretch(1)
        row.addWidget(self.fit)
        row.addWidget(save)
        row.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addLayout(row)
        screen = self.screen().availableGeometry() if self.screen() else None
        w = min(self.pixmap.width() + 60, int(screen.width() * 0.85) if screen else 1200)
        h = min(self.pixmap.height() + 100, int(screen.height() * 0.85) if screen else 800)
        self.resize(max(w, 360), max(h, 240))
        self._show()

    def _show(self, *_args) -> None:
        if self.pixmap.isNull():
            self.label.setText("This image could not be loaded.")
            return
        if self.fit.isChecked():
            self.label.setPixmap(self.pixmap)
        else:
            area = self.scroll.viewport().size()
            self.label.setPixmap(self.pixmap.scaled(
                area, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
                if self.pixmap.width() > area.width() or self.pixmap.height() > area.height()
                else self.pixmap)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._show()

    def _save(self) -> None:
        suggested = (self.att.filename if self.att and self.att.filename and
                     "." in self.att.filename else "image.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save image", suggested)
        if path:
            Path(path).write_bytes(self.data)


# =========================================================================== #
# Entry cards and the log
# =========================================================================== #


class EntryCard(QFrame):
    editRequested = Signal(int)
    deleteRequested = Signal(int)
    moveRequested = Signal(int, int)  # entry id, delta
    collapseToggled = Signal(int, bool)

    def __init__(self, doc: ProjectDocument, entry: LogEntry, users: dict, collapsed: bool,
                 manual: bool, first: bool, last: bool, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("entryCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        head = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setAutoRaise(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)
        self.toggle.setToolTip("Collapse / expand")
        self.toggle.clicked.connect(lambda: self.collapseToggled.emit(entry.id, not collapsed))
        head.addWidget(self.toggle)
        label = bone_label_fn(doc)
        title = entry.title or summary_line(entry.body_md, label) or "(empty entry)"
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        if manual:
            for text, delta, enabled in (("▲", -1, not first), ("▼", +1, not last)):
                b = QToolButton()
                b.setText(text)
                b.setAutoRaise(True)
                b.setEnabled(enabled)
                b.setToolTip("Move up" if delta < 0 else "Move down")
                b.clicked.connect(lambda _=False, d=delta: self.moveRequested.emit(entry.id, d))
                head.addWidget(b)
        for text, tip, sig in (("Edit", "Edit this entry", self.editRequested),
                               ("Delete", "Delete this entry", self.deleteRequested)):
            b = QToolButton()
            b.setText(text)
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, s=sig: s.emit(entry.id))
            head.addWidget(b)
        layout.addLayout(head)

        def who(uid):
            return users[uid].display_name if uid in users else "unknown"

        meta = f"{local_time(entry.created_at)} · {who(entry.created_by)}"
        if entry.updated_at != entry.created_at:
            meta += f" · edited {local_time(entry.updated_at)}"
            if entry.updated_by != entry.created_by:
                meta += f" by {who(entry.updated_by)}"
        self.meta = QLabel(meta)
        theme.mark(self.meta, muted=True, small=True)
        layout.addWidget(self.meta)

        if collapsed:
            if entry.title:
                summary = QLabel(summary_line(entry.body_md, label))
                theme.mark(summary, muted=True)
                summary.setWordWrap(True)
                layout.addWidget(summary)
            self.body = None
        else:
            self.body = make_markdown_view(doc, auto_height=True, parent=self)
            self.body.set_markdown(entry.body_md)
            layout.addWidget(self.body)

    def mouseDoubleClickEvent(self, e) -> None:
        self.editRequested.emit(self.entry.id)


class EvidenceLog(QWidget):
    """The evidence log of one cause, shown in the details panel."""

    boneLinkClicked = Signal(int)

    def __init__(self, settings: QSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.doc: ProjectDocument | None = None
        self.bone_id: int | None = None
        self.settings = settings
        self.collapsed: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.heading = QLabel("Evidence log")
        self.heading.setStyleSheet("font-weight: 600;")
        head.addWidget(self.heading)
        head.addStretch(1)
        self.order = QComboBox()
        self.order.addItem("Manual order", "manual")
        self.order.addItem("By date", "date")
        self.order.setToolTip("Show entries in your own order, or oldest first by date")
        saved = settings.value(ORDER_KEY, "manual") if settings else "manual"
        self.order.setCurrentIndex(max(0, self.order.findData(saved)))
        self.order.currentIndexChanged.connect(self._order_changed)
        head.addWidget(self.order)
        self.fold_btn = QToolButton()
        self.fold_btn.setText("Collapse all")
        self.fold_btn.setAutoRaise(True)
        self.fold_btn.clicked.connect(self._fold_all)
        head.addWidget(self.fold_btn)
        self.add_btn = QPushButton("Add entry")
        self.add_btn.setToolTip("Add a finding, data or analysis to this cause")
        self.add_btn.clicked.connect(self.add_entry)
        head.addWidget(self.add_btn)
        layout.addLayout(head)

        self.cards_host = QWidget()
        self.cards = QVBoxLayout(self.cards_host)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(6)
        layout.addWidget(self.cards_host)
        self.empty = QLabel("No evidence yet. Add an entry to record findings, data, images "
                            "or analysis for this cause.")
        self.empty.setWordWrap(True)
        theme.mark(self.empty, muted=True)
        self.empty.setContentsMargins(2, 6, 2, 6)
        layout.addWidget(self.empty)

    # ----- wiring --------------------------------------------------------- #

    def set_document(self, doc: ProjectDocument | None) -> None:
        self.doc = doc
        self.bone_id = None
        self.collapsed.clear()
        if doc is not None:
            doc.logChanged.connect(self._log_changed)
            doc.choicesChanged.connect(self.refresh)

    def show_bone(self, bone_id: int | None) -> None:
        self.bone_id = bone_id
        self.refresh()

    def _log_changed(self, bone_id: int) -> None:
        if bone_id == self.bone_id:
            self.refresh()

    def manual_order(self) -> bool:
        return self.order.currentData() == "manual"

    def entries(self) -> list[LogEntry]:
        if not self.doc or self.bone_id is None:
            return []
        entries = self.doc.log_entries(self.bone_id)
        if not self.manual_order():
            entries.sort(key=lambda e: (e.created_at, e.id))
        return entries

    # ----- display -------------------------------------------------------- #

    def refresh(self) -> None:
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()
        entries = self.entries()
        self.heading.setText(f"Evidence log ({len(entries)})")
        self.empty.setVisible(not entries)
        self.fold_btn.setVisible(bool(entries))
        self.fold_btn.setText("Expand all" if entries and all(
            e.id in self.collapsed for e in entries) else "Collapse all")
        if not self.doc:
            return
        users = self.doc.repo.users()
        manual = self.manual_order()
        for i, e in enumerate(entries):
            card = EntryCard(self.doc, e, users, e.id in self.collapsed, manual,
                             first=i == 0, last=i == len(entries) - 1)
            card.editRequested.connect(self.edit_entry)
            card.deleteRequested.connect(self.delete_entry)
            card.moveRequested.connect(self.move_entry)
            card.collapseToggled.connect(self._toggle)
            if card.body is not None:
                card.body.boneClicked.connect(self.boneLinkClicked)
            self.cards.addWidget(card)

    def card_for(self, entry_id: int) -> EntryCard | None:
        for i in range(self.cards.count()):
            w = self.cards.itemAt(i).widget()
            if isinstance(w, EntryCard) and w.entry.id == entry_id:
                return w
        return None

    def _toggle(self, entry_id: int, collapse: bool) -> None:
        (self.collapsed.add if collapse else self.collapsed.discard)(entry_id)
        self.refresh()

    def _fold_all(self) -> None:
        ids = {e.id for e in self.entries()}
        if ids <= self.collapsed:
            self.collapsed -= ids
        else:
            self.collapsed |= ids
        self.refresh()

    def _order_changed(self, _index: int) -> None:
        if self.settings is not None:
            self.settings.setValue(ORDER_KEY, self.order.currentData())
        self.refresh()

    # ----- edits ---------------------------------------------------------- #

    def add_entry(self) -> int | None:
        if not self.doc or self.bone_id is None:
            return None
        editor = EntryEditor(self.doc, self.bone_id, parent=self.window())
        if editor.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.doc.add_log_entry(self.bone_id, editor.body(), editor.entry_title(),
                                      attachments=editor.new_attachments)

    def edit_entry(self, entry_id: int) -> bool:
        if not self.doc:
            return False
        entry = self.doc.repo.log_entry(entry_id)
        editor = EntryEditor(self.doc, entry.bone_id, entry, parent=self.window())
        if editor.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.doc.update_log_entry(entry_id, attachments=editor.new_attachments,
                                         title=editor.entry_title(), body_md=editor.body())

    def delete_entry(self, entry_id: int) -> None:
        if not self.doc:
            return
        entry = self.doc.repo.log_entry(entry_id)
        name = entry.title or summary_line(entry.body_md) or "this entry"
        if QMessageBox.question(self, "Delete entry",
                                f"Delete “{name}”?\n\nYou can undo this.") == \
                QMessageBox.StandardButton.Yes:
            self.doc.delete_log_entry(entry_id)

    def move_entry(self, entry_id: int, delta: int) -> None:
        if self.doc:
            pos = self.doc.repo.log_entry(entry_id).position
            self.doc.move_log_entry(entry_id, pos + delta)


# =========================================================================== #
# Timeline
# =========================================================================== #


class TimelineDialog(QDialog):
    """Every evidence entry in the project, in date order (FR-5.6)."""

    openEntry = Signal(int, int)  # bone id, entry id

    def __init__(self, doc: ProjectDocument, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Evidence timeline")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.count = QLabel()
        top.addWidget(self.count)
        top.addStretch(1)
        self.newest_first = QAction("Newest first", self, checkable=True)
        newest = QToolButton()
        newest.setDefaultAction(self.newest_first)
        self.newest_first.toggled.connect(self.refresh)
        top.addWidget(newest)
        layout.addLayout(top)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["When", "Bone", "Entry", "Author"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 140)
        header.resizeSection(1, 260)
        self.tree.itemActivated.connect(self._activated)
        layout.addWidget(self.tree, 1)
        hint = QLabel("Double-click an entry to show it in the editor.")
        theme.mark(hint, muted=True)
        layout.addWidget(hint)
        doc.logChanged.connect(self.refresh)
        doc.changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        doc = self.doc
        label = bone_label_fn(doc)
        users = doc.repo.users()
        entries = doc.repo.all_log_entries()
        if self.newest_first.isChecked():
            entries.reverse()
        self.tree.clear()
        for e in entries:
            author = users[e.created_by].display_name if e.created_by in users else ""
            text = e.title or summary_line(e.body_md, label) or "(empty entry)"
            item = QTreeWidgetItem([local_time(e.created_at), label(e.bone_id) or "",
                                    text, author])
            item.setData(0, Qt.ItemDataRole.UserRole, (e.bone_id, e.id))
            item.setToolTip(2, summary_line(e.body_md, label, limit=400))
            self.tree.addTopLevelItem(item)
        n = len(entries)
        self.count.setText(f"{n} entr{'y' if n == 1 else 'ies'} across "
                           f"{len({e.bone_id for e in entries})} causes")

    def _activated(self, item: QTreeWidgetItem, _col: int) -> None:
        bone_id, entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        self.openEntry.emit(bone_id, entry_id)


__all__ = ["EntryEditor", "EvidenceLog", "ImageViewer", "TimelineDialog", "png_bytes"]
