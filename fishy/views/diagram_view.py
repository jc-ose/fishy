"""Qt rendering of a laid-out fishbone `Diagram`.

`DiagramScene` turns layout primitives into graphics items; `DiagramView` adds
zoom, pan, fit and export. The layout itself happens in `fishy.layout`, which
knows nothing about Qt. The only coupling is the text-measurement function
from `qt_measure`, which makes the layout reserve exactly the space that Qt
will use to draw each label.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QMarginsF, QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPdfWriter,
    QPen,
)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from fishy.layout import Diagram, Label, LabelKind, Line, LineKind, MeasureFn, Rect

FONT_FAMILY = "Segoe UI"  # Windows default UI font; Qt falls back elsewhere
TEXT_FLAGS = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
BONE_ID_KEY = 0  # QGraphicsItem.data() slot holding the bone id

LINE_WIDTHS = {LineKind.SPINE: 3.0, LineKind.MAIN: 2.2, LineKind.BRANCH: 1.3}
SELECT_COLOR = QColor("#1e88e5")


def make_font(size: float, *, bold: bool = False, strike: bool = False) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPointSizeF(size)
    font.setBold(bold)
    font.setStrikeOut(strike)
    return font


def qt_measure() -> MeasureFn:
    """A `MeasureFn` backed by `QFontMetricsF`, cached per (text, width, size)."""

    @lru_cache(maxsize=4096)
    def measure(text: str, max_width: float, font_size: float,
                bold: bool = False) -> tuple[float, float]:
        fm = QFontMetricsF(make_font(font_size, bold=bold))
        r = fm.boundingRect(QRectF(0, 0, max_width, 1e6), TEXT_FLAGS, text)
        return r.width() + 1.0, r.height()  # +1: avoid wrap from rounding

    return measure


def _qrect(r: Rect) -> QRectF:
    return QRectF(r.x, r.y, r.w, r.h)


def _faded(color: str, dimmed: bool) -> QColor:
    c = QColor(color)
    if dimmed:
        c.setAlphaF(0.55)
    return c


PROGRESS_DONE = QColor("#43a047")
PROGRESS_TRACK = QColor("#cfd8dc")


def draw_progress_ring(painter: QPainter, r: QRectF, done: int, total: int) -> None:
    """Category progress (FR-3a.6): a ring filled clockwise from 12 o'clock."""
    width = max(2.0, r.width() * 0.2)
    inner = r.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(PROGRESS_TRACK, width)
    painter.setPen(pen)
    painter.drawEllipse(inner)
    if total and done:
        pen.setColor(PROGRESS_DONE)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawArc(inner, 90 * 16, -int(360 * 16 * done / total))


def draw_pin(painter: QPainter, r: QRectF, color: QColor) -> None:
    """Pin marker for a level that overrides roll-up (FR-3.7): head + needle."""
    head = QRectF(r.left() + r.width() * 0.15, r.top(), r.width() * 0.7, r.width() * 0.7)
    needle = QPen(color.darker(130), max(1.0, r.width() * 0.14))
    needle.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(needle)
    needle_top = QPointF(head.center().x(), head.bottom() - 1)
    painter.drawLine(needle_top, QPointF(head.center().x(), r.bottom()))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(head)


class BoneLineItem(QGraphicsLineItem):
    def __init__(self, line: Line) -> None:
        super().__init__(line.x1, line.y1, line.x2, line.y2)
        pen = QPen(_faded(line.color, line.dimmed), LINE_WIDTHS[line.kind])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if line.dimmed:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(0)
        if line.bone_id is not None:
            self.setData(BONE_ID_KEY, line.bone_id)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def shape(self) -> QPainterPath:  # generous hit area for thin lines
        path = QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(path)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        painter.setPen(self.pen())
        painter.drawLine(self.line())


class LabelItem(QGraphicsItem):
    def __init__(self, label: Label) -> None:
        super().__init__()
        self.label = label
        self.rect = _qrect(label.rect)
        self.setZValue(1)
        if label.bone_id is not None:
            self.setData(BONE_ID_KEY, label.bone_id)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label.text)

    # Selection outline is drawn 3 px outside the box with a 1.5 px pen (plus
    # antialiasing), so the bounds must cover that or Qt leaves a ghost behind
    # when the outline is erased.
    OUTLINE_MARGIN = 6.0

    def boundingRect(self) -> QRectF:
        m = self.OUTLINE_MARGIN
        return self.rect.adjusted(-m, -m, m, m)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        lab = self.label
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = _faded(lab.color, lab.dimmed)

        if lab.kind is LabelKind.HEAD:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#263238"))
            painter.drawRoundedRect(self.rect, 6, 6)
            text_color = QColor("white")
            font = make_font(lab.font_size, bold=True)
        elif lab.kind is LabelKind.CATEGORY:
            fill = QColor(lab.color)
            fill.setAlphaF(0.10)
            painter.setPen(QPen(QColor(lab.color), 1.6))
            painter.setBrush(fill)
            painter.drawRoundedRect(self.rect, 5, 5)
            font = make_font(lab.font_size, bold=True)
        else:
            font = make_font(lab.font_size, strike=lab.dimmed)

        if lab.status_dot is not None and lab.status_color:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(lab.status_color))
            painter.drawEllipse(_qrect(lab.status_dot))
        if lab.progress_rect is not None and lab.progress:
            draw_progress_ring(painter, _qrect(lab.progress_rect), *lab.progress)
        if lab.pin_mark is not None:
            draw_pin(painter, _qrect(lab.pin_mark), QColor(lab.color))

        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(_qrect(lab.text_rect), TEXT_FLAGS, lab.text)

        if lab.badge:
            bfont = make_font(lab.font_size * 0.85, bold=True)
            fm = QFontMetricsF(bfont)
            bw = fm.horizontalAdvance(lab.badge) + 8
            bh = fm.height() + 2
            tr = _qrect(lab.text_rect)
            pill = QRectF(self.rect.right() - bw, tr.top() + (min(tr.height(), bh * 1.4) - bh) / 2,
                          bw, bh)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#cfd8dc"))
            painter.drawRoundedRect(pill, bh / 2, bh / 2)
            painter.setFont(bfont)
            painter.setPen(QColor("#37474f"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, lab.badge)

        if self.isSelected():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(SELECT_COLOR, 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(self.rect.adjusted(-3, -3, 3, 3), 4, 4)


class DiagramScene(QGraphicsScene):
    """Holds the items for one `Diagram` and reports bone selection."""

    boneSelected = Signal(object)  # bone id (int) or None
    boneActivated = Signal(int)  # double-click: jump to editing this bone
    boneContextMenu = Signal(int, QPoint)  # right-click: bone id, global screen position

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(QColor("white")))
        self._labels: dict[int, LabelItem] = {}
        self.legend_item = None
        self._muted = False
        self.selectionChanged.connect(self._on_selection_changed)

    def set_diagram(self, diagram: Diagram, legend=None, legend_position: str = "bottom-left"
                    ) -> None:
        """Show `diagram`, plus an optional `LegendData` placed beside or below it."""
        from .legend import place_legend  # local import: legend.py imports this module

        selected = self.selected_bone()
        self._muted = True
        self.clear()
        self._labels.clear()
        self.legend_item = None
        for line in diagram.lines:
            self.addItem(BoneLineItem(line))
        for label in diagram.labels:
            item = LabelItem(label)
            self.addItem(item)
            if label.bone_id is not None:
                self._labels[label.bone_id] = item
        b = diagram.bounds
        rect = QRectF(b.x, b.y, b.w, b.h)
        if legend is not None and legend.levels:
            self.legend_item = place_legend(legend, legend_position, rect)
            self.addItem(self.legend_item)
            rect = rect.united(self.legend_item.sceneBoundingRect())
        self.setSceneRect(rect.marginsAdded(QMarginsF(30, 30, 30, 30)))
        self._muted = False
        if selected is not None:
            self.select_bone(selected)

    def selected_bone(self) -> int | None:
        for item in self.selectedItems():
            bone = item.data(BONE_ID_KEY)
            if bone is not None:
                return int(bone)
        return None

    def select_bone(self, bone_id: int | None) -> LabelItem | None:
        self._muted = True
        self.clearSelection()
        item = self._labels.get(bone_id) if bone_id is not None else None
        if item is not None:
            item.setSelected(True)
        self._muted = False
        self.boneSelected.emit(bone_id if item else None)
        return item

    def _bone_at(self, scene_pos: QPointF) -> int | None:
        for item in self.items(scene_pos):
            bone = item.data(BONE_ID_KEY)
            if bone is not None:
                return int(bone)
        return None

    def mouseDoubleClickEvent(self, event) -> None:
        bone = self._bone_at(event.scenePos())
        if bone is not None:
            self.select_bone(bone)
            self.boneActivated.emit(bone)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        bone = self._bone_at(event.scenePos())
        if bone is not None:
            self.select_bone(bone)
            self.boneContextMenu.emit(bone, event.screenPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def _on_selection_changed(self) -> None:
        if self._muted:
            return
        bone = self.selected_bone()
        # Clicking a line selects its bone's label, so highlight lives in one place.
        if bone is not None and bone in self._labels and not self._labels[bone].isSelected():
            self.select_bone(bone)
            return
        self.boneSelected.emit(bone)


class DiagramView(QGraphicsView):
    """Zoom (wheel), pan (drag background), fit, zoom-to-selection, export."""

    MIN_SCALE, MAX_SCALE = 0.1, 6.0

    def __init__(self, scene: DiagramScene, parent=None) -> None:
        super().__init__(scene, parent)
        self._scene = scene  # keep the Python wrapper alive alongside the view
        self._fit_pending = True
        # While True, the diagram stays fitted as the view resizes or the diagram
        # changes. Zooming by hand turns it off; Fit turns it back on.
        self.auto_fit = True
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_scale = self.transform().m11() * factor
        if self.MIN_SCALE <= new_scale <= self.MAX_SCALE:
            self.auto_fit = False
            self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.auto_fit:
            self.fit_all()

    def fit_all(self) -> None:
        if not self.isVisible():  # no real viewport size yet: fit once shown
            self._fit_pending = True
            return
        self._fit_pending = False
        self.auto_fit = True
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def request_fit(self) -> None:
        """Fit now if visible, otherwise as soon as the view is first shown."""
        self.fit_all()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._fit_pending:
            QTimer.singleShot(0, self.fit_all)  # after the layout has sized us

    def zoom_to_selection(self) -> None:
        """Zoom in on the selected bone with some context around it (FR-2.6)."""
        items = self.scene().selectedItems()
        if not items:
            return
        self.auto_fit = False
        rect = items[0].sceneBoundingRect().adjusted(-220, -140, 220, 140)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.6:  # don't zoom in absurdly far on a small label
            self.resetTransform()
            self.scale(1.6, 1.6)
            self.centerOn(items[0])

    # ----- export (FR-8.1) ------------------------------------------------- #

    def _render_clean(self, painter: QPainter, target: QRectF, source: QRectF,
                      mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio) -> None:
        """Render without the on-screen selection outline, then restore the selection."""
        scene = self.scene()
        selected = scene.selected_bone()
        scene._muted = True
        scene.clearSelection()
        scene.render(painter, target, source, mode)
        if selected is not None:
            scene.select_bone(selected)
        scene._muted = False

    def export_png(self, path: str, scale: float = 2.0) -> None:
        src = self.scene().sceneRect()
        image = QImage((src.size() * scale).toSize(), QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing
                               | QPainter.RenderHint.TextAntialiasing)
        self._render_clean(painter, QRectF(image.rect()), src)
        painter.end()
        image.save(path)

    def export_svg(self, path: str) -> None:
        src = self.scene().sceneRect()
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(src.size().toSize())
        gen.setViewBox(QRectF(QPointF(0, 0), src.size()))
        gen.setResolution(96)  # point-sized fonts: match the screen (see export.diagram)
        gen.setTitle("Fishbone diagram")
        painter = QPainter(gen)
        self._render_clean(painter, QRectF(QPointF(0, 0), src.size()), src)
        painter.end()

    def export_pdf(self, path: str) -> None:
        src = self.scene().sceneRect()
        writer = QPdfWriter(path)
        writer.setResolution(96)  # point-sized fonts: match the screen (see export.diagram)
        orientation = (QPageLayout.Orientation.Landscape if src.width() >= src.height()
                       else QPageLayout.Orientation.Portrait)
        writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.Letter), orientation,
                                         QMarginsF(10, 10, 10, 10), QPageLayout.Unit.Millimeter))
        writer.setTitle("Fishbone diagram")
        painter = QPainter(writer)
        target = QRectF(QPointF(0, 0), QSizeF(writer.width(), writer.height()))
        self._render_clean(painter, target, src)
        painter.end()
