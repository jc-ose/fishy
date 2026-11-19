"""Read-only markdown viewer for evidence entries (FR-5.2, FR-5.3).

Renders with `fishy.markdown_render`, loads ``attachment:<id>`` images from the
project file (scaled to fit), and turns clicks into signals: an image opens the
full-size viewer, a bone link selects that bone. Web links open in the browser.

With `auto_height=True` the widget sizes itself to its content (no scrollbar),
which is how entry cards stack inside the details panel.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QTextDocument
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser

from fishy import theme
from fishy.markdown_render import ATTACHMENT_SCHEME, BONE_SCHEME, BoneLabel, render, stylesheet

AttachmentLoader = Callable[[int], bytes | None]
MAX_PREVIEW_WIDTH = 560


class MarkdownView(QTextBrowser):
    imageClicked = Signal(int)  # attachment id
    boneClicked = Signal(int)  # bone id

    def __init__(self, parent=None, auto_height: bool = False) -> None:
        super().__init__(parent)
        self.load_attachment: AttachmentLoader | None = None
        self.bone_label: BoneLabel | None = None
        self.auto_height = auto_height
        self._markdown = ""
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._link_clicked)
        self.document().setDefaultStyleSheet(stylesheet(theme.current().dark))
        self.document().setDocumentMargin(2)
        if (mgr := theme.manager()) is not None:
            mgr.changed.connect(self._theme_changed)
        if auto_height:
            self.setFrameShape(QFrame.Shape.NoFrame)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.viewport().setAutoFillBackground(False)
            self.setStyleSheet("QTextBrowser { background: transparent; }")
            self.document().documentLayout().documentSizeChanged.connect(self._fit_height)

    def set_markdown(self, md: str) -> None:
        self._markdown = md or ""
        # Images are cached per document; drop them so edited entries reload.
        self.document().clear()
        dark = theme.current().dark
        self.document().setDefaultStyleSheet(stylesheet(dark))
        self.setHtml(render(self._markdown, self.bone_label, dark))
        self._fit_height()

    def _theme_changed(self) -> None:
        self.set_markdown(self._markdown)

    def markdown(self) -> str:
        return self._markdown

    # ----- images ---------------------------------------------------------- #

    def _preview_width(self) -> int:
        return max(120, min(MAX_PREVIEW_WIDTH, self.viewport().width() - 16))

    def loadResource(self, kind: int, url: QUrl):
        if kind == QTextDocument.ResourceType.ImageResource.value and \
                url.scheme() == ATTACHMENT_SCHEME:
            image = self._load_image(url)
            if image is not None:
                return image
            return _missing_image()
        return super().loadResource(kind, url)

    def _load_image(self, url: QUrl) -> QImage | None:
        if self.load_attachment is None:
            return None
        try:
            data = self.load_attachment(int(url.path()))
        except (KeyError, ValueError):
            return None
        if not data:
            return None
        image = QImage.fromData(data)
        if image.isNull():
            return None
        width = self._preview_width()
        if image.width() > width:
            image = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        return image

    # ----- links ----------------------------------------------------------- #

    def _link_clicked(self, url: QUrl) -> None:
        scheme = url.scheme()
        try:
            if scheme == ATTACHMENT_SCHEME:
                self.imageClicked.emit(int(url.path()))
            elif scheme == BONE_SCHEME:
                self.boneClicked.emit(int(url.path()))
            elif scheme in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
        except ValueError:
            pass

    # ----- auto height ----------------------------------------------------- #

    def _fit_height(self, *_args) -> None:
        if not self.auto_height:
            return
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        h = int(doc.size().height()) + 4
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.auto_height:
            if e.oldSize().width() != e.size().width() and self._markdown:
                self.set_markdown(self._markdown)  # re-scale images to the new width
            self._fit_height()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.auto_height:
            hint.setHeight(int(self.document().size().height()) + 4)
        return hint


def _missing_image() -> QImage:
    from PySide6.QtGui import QColor, QPainter

    img = QImage(160, 40, QImage.Format.Format_ARGB32)
    t = theme.current()
    img.fill(QColor(t.alt_base))
    p = QPainter(img)
    p.setPen(QColor(t.muted))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "image not found")
    p.end()
    return img
