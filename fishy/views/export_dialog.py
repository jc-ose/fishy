"""Export dialog (FR-8.1 – 8.4): format, contents, size, with a live preview.

Every choice is remembered between sessions (app settings), except what defaults
from the project itself: the title starts as the project name, and the legend and
status checkboxes start from the project's own display settings.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPageLayout, QPageSize, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.export import (
    ORIENTATIONS,
    PAGE_SIZES,
    DiagramExportOptions,
    build_scene,
    default_options,
    export_file,
    png_size,
    render_image,
)
from fishy.export.diagram import render_into, resolve_orientation
from fishy.layout import MeasureFn

FORMATS = (("png", "PNG image"), ("svg", "SVG (vector)"), ("pdf", "PDF (vector)"))
SCALES = ((1.0, "1× (screen)"), (2.0, "2× (sharp)"), (3.0, "3×"), (4.0, "4× (print)"))
PREVIEW_W, PREVIEW_H = 560, 300


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return cleaned or "fishbone"


class ExportDialog(QDialog):
    def __init__(self, doc: ProjectDocument, measure: MeasureFn,
                 settings: QSettings | None = None, project_path: str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export diagram")
        self.doc, self.measure, self.settings = doc, measure, settings
        self.project_path = project_path
        self.last_path: str | None = None
        self.copied = False
        base = default_options(doc)

        # ----- format ----------------------------------------------------- #
        fmt_box = QGroupBox("Format")
        fmt_row = QHBoxLayout(fmt_box)
        self.format_group = QButtonGroup(self)
        self.format_buttons: dict[str, QRadioButton] = {}
        for key, label in FORMATS:
            b = QRadioButton(label)
            self.format_group.addButton(b)
            self.format_buttons[key] = b
            fmt_row.addWidget(b)
        fmt_row.addStretch(1)
        self.format_buttons[self._get("format", "png")
                            if self._get("format", "png") in self.format_buttons
                            else "png"].setChecked(True)

        # ----- contents --------------------------------------------------- #
        content = QGroupBox("Contents")
        form = QFormLayout(content)
        self.title_block = QCheckBox("Title block")
        self.title_block.setChecked(self._get_bool("title_block", True))
        self.title_edit = QLineEdit(base.title)
        self.title_edit.setPlaceholderText("Title (project name)")
        title_row = QHBoxLayout()
        title_row.addWidget(self.title_block)
        title_row.addWidget(self.title_edit, 1)
        form.addRow(title_row)
        self.show_date = QCheckBox("Date")
        self.show_date.setChecked(self._get_bool("show_date", True))
        self.show_author = QCheckBox(f"Author ({base.author})" if base.author else "Author")
        self.show_author.setChecked(self._get_bool("show_author", True))
        self.author = base.author
        meta_row = QHBoxLayout()
        meta_row.addSpacing(22)
        meta_row.addWidget(self.show_date)
        meta_row.addWidget(self.show_author)
        meta_row.addStretch(1)
        form.addRow(meta_row)
        self.legend = QCheckBox("Legend")
        self.legend.setChecked(base.legend)
        self.status = QCheckBox("Status dots and key")
        self.status.setChecked(base.status)
        self.expanded = QCheckBox("Fully expanded (include collapsed bones)")
        has_collapsed = any(b.collapsed and doc.tree.kids(b.id)
                            for b in doc.tree.bones.values())
        self.expanded.setChecked(has_collapsed and self._get_bool("expanded", False))
        self.expanded.setEnabled(has_collapsed)
        if not has_collapsed:
            self.expanded.setToolTip("No bones are collapsed, so the export is already complete.")
        row = QHBoxLayout()
        row.addWidget(self.legend)
        row.addWidget(self.status)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(self.expanded)

        # ----- size / page (depends on format) ---------------------------- #
        self.size_stack = QStackedWidget()
        png_page = QWidget()
        png_form = QFormLayout(png_page)
        png_form.setContentsMargins(0, 0, 0, 0)
        self.scale = QComboBox()
        for value, label in SCALES:
            self.scale.addItem(label, value)
        self.scale.setCurrentIndex(max(0, self.scale.findData(float(self._get("scale", 2.0)))))
        self.png_info = theme.mark(QLabel(), muted=True)
        png_row = QHBoxLayout()
        png_row.addWidget(self.scale)
        png_row.addWidget(self.png_info, 1)
        png_form.addRow("Scale", png_row)
        self.size_stack.addWidget(png_page)

        svg_page = QWidget()
        svg_layout = QVBoxLayout(svg_page)
        svg_layout.setContentsMargins(0, 0, 0, 0)
        svg_layout.addWidget(theme.mark(QLabel(
            "Vector image: scales to any size. Opens in browsers, Inkscape, Visio and "
            "Office (Insert → Pictures)."), muted=True))
        self.size_stack.addWidget(svg_page)

        pdf_page = QWidget()
        pdf_form = QFormLayout(pdf_page)
        pdf_form.setContentsMargins(0, 0, 0, 0)
        self.page = QComboBox()
        self.page.addItems(list(PAGE_SIZES))
        self.page.setCurrentText(self._get("page", "Letter"))
        self.orientation = QComboBox()
        for key in ORIENTATIONS:
            self.orientation.addItem(key.capitalize(), key)
        self.orientation.setCurrentIndex(
            max(0, self.orientation.findData(self._get("orientation", "auto"))))
        page_row = QHBoxLayout()
        page_row.addWidget(self.page)
        page_row.addWidget(QLabel("Orientation"))
        page_row.addWidget(self.orientation)
        page_row.addStretch(1)
        pdf_form.addRow("Page", page_row)
        pdf_form.addRow("", theme.mark(QLabel("One page; the diagram is scaled to fit and "
                                              "centered."), muted=True))
        self.size_stack.addWidget(pdf_page)
        size_box = QGroupBox("Size")
        size_layout = QVBoxLayout(size_box)
        size_layout.addWidget(self.size_stack)

        # ----- preview ---------------------------------------------------- #
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview.setStyleSheet("background: white;")

        # ----- buttons ---------------------------------------------------- #
        buttons = QDialogButtonBox()
        self.copy_btn = QPushButton("Copy image")
        self.copy_btn.setToolTip("Copy the diagram to the clipboard as an image (FR-8.4)")
        self.export_btn = QPushButton("Export…")
        self.export_btn.setDefault(True)
        buttons.addButton(self.copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.export_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        self.export_btn.clicked.connect(self.export)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)

        left = QVBoxLayout()
        left.addWidget(fmt_box)
        left.addWidget(content)
        left.addWidget(size_box)
        left.addStretch(1)
        body = QHBoxLayout()
        body.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(QLabel("Preview"))
        right.addWidget(self.preview)
        right.addStretch(1)
        body.addLayout(right)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(buttons)

        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(self.refresh)
        for w in (self.title_block, self.show_date, self.show_author, self.legend, self.status,
                  self.expanded):
            w.toggled.connect(self._changed)
        self.title_edit.textChanged.connect(self._changed)
        self.format_group.buttonToggled.connect(self._changed)
        for combo in (self.scale, self.page, self.orientation):
            combo.currentIndexChanged.connect(self._changed)
        self.scene = None
        self.refresh()

    # ----- settings ------------------------------------------------------- #

    def _get(self, key: str, default):
        return self.settings.value(f"export/{key}", default) if self.settings else default

    def _get_bool(self, key: str, default: bool) -> bool:
        v = self._get(key, default)
        return v if isinstance(v, bool) else str(v).lower() == "true"

    def _save_settings(self) -> None:
        if not self.settings:
            return
        for key, value in (("format", self.format()), ("title_block", self.title_block.isChecked()),
                           ("show_date", self.show_date.isChecked()),
                           ("show_author", self.show_author.isChecked()),
                           ("expanded", self.expanded.isChecked()),
                           ("scale", self.scale.currentData()), ("page", self.page.currentText()),
                           ("orientation", self.orientation.currentData())):
            self.settings.setValue(f"export/{key}", value)

    # ----- state ---------------------------------------------------------- #

    def format(self) -> str:
        return next(k for k, b in self.format_buttons.items() if b.isChecked())

    def options(self) -> DiagramExportOptions:
        return DiagramExportOptions(
            title_block=self.title_block.isChecked(),
            title=self.title_edit.text().strip() or self.doc.project.name,
            show_date=self.show_date.isChecked(),
            author=self.author if self.show_author.isChecked() else "",
            legend=self.legend.isChecked(), status=self.status.isChecked(),
            expanded=self.expanded.isChecked())

    def _changed(self, *_args) -> None:
        self._timer.start()

    def refresh(self) -> None:
        fmt = self.format()
        self.size_stack.setCurrentIndex([k for k, _ in FORMATS].index(fmt))
        on = self.title_block.isChecked()
        for w in (self.title_edit, self.show_date, self.show_author):
            w.setEnabled(on)
        self.scene = build_scene(self.doc, self.measure, self.options())
        w, h = png_size(self.scene, self.scale.currentData())
        self.png_info.setText(f"{w:,} × {h:,} px")
        if fmt == "pdf":
            self.preview.setPixmap(self._page_preview())
            return
        image = render_image(self.scene, 1.0)
        pix = QPixmap.fromImage(image).scaled(
            PREVIEW_W - 8, PREVIEW_H - 8, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _page_preview(self) -> QPixmap:
        """The PDF page as it will print: page shape, margins, diagram centered."""
        size = QPageSize(PAGE_SIZES[self.page.currentText()]).size(QPageSize.Unit.Millimeter)
        w_mm, h_mm = size.width(), size.height()
        if (resolve_orientation(self.scene, self.orientation.currentData())
                == QPageLayout.Orientation.Landscape):
            w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
        else:
            w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
        k = min((PREVIEW_W - 24) / w_mm, (PREVIEW_H - 24) / h_mm)
        pix = QPixmap(PREVIEW_W - 8, PREVIEW_H - 8)
        pix.fill(QColor("#d5d9dd"))
        page = QRectF(0, 0, w_mm * k, h_mm * k)
        page.moveCenter(QRectF(pix.rect()).center())
        painter = QPainter(pix)
        painter.fillRect(page.translated(2, 2), QColor(0, 0, 0, 40))  # shadow
        painter.fillRect(page, QColor("white"))
        margin = 10 * k
        render_into(self.scene, painter, page.adjusted(margin, margin, -margin, -margin))
        painter.end()
        return pix

    # ----- actions -------------------------------------------------------- #

    def suggested_path(self) -> str:
        folder = self._get("folder", "") or (str(Path(self.project_path).parent)
                                             if self.project_path else "")
        name = f"{safe_filename(self.doc.project.name)}.{self.format()}"
        return str(Path(folder) / name) if folder else name

    def export(self, path: str | None = None) -> bool:
        """Export to `path`, or ask for one. Returns True when a file was written."""
        if self._timer.isActive():
            self._timer.stop()
            self.refresh()
        fmt = self.format()
        if not path:
            filters = {"png": "PNG image (*.png)", "svg": "SVG image (*.svg)",
                       "pdf": "PDF document (*.pdf)"}
            path, _ = QFileDialog.getSaveFileName(self, "Export diagram", self.suggested_path(),
                                                  filters[fmt])
            if not path:
                return False
        if Path(path).suffix.lower() != f".{fmt}":
            path += f".{fmt}"
        try:
            export_file(self.scene, path, scale=self.scale.currentData(),
                        page=self.page.currentText(), orientation=self.orientation.currentData(),
                        title=self.options().title)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return False
        self.last_path = path
        self._save_settings()
        if self.settings:
            self.settings.setValue("export/folder", str(Path(path).parent))
        self.accept()
        return True

    def copy_to_clipboard(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.refresh()
        QGuiApplication.clipboard().setImage(render_image(self.scene, 2.0))
        self.copied = True
        self._save_settings()
        self.accept()
