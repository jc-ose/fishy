"""Diagram legend (FR-3.5, FR-2.10): levels with definitions, plus a status key.

The legend is a scene item placed relative to the diagram's bounds, so it
appears in PNG/SVG/PDF exports automatically. Below the diagram it flows into
as many columns as fit the diagram's width; on the right it's a single column.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from .diagram_view import TEXT_FLAGS, make_font

NAME_SIZE = 9.5
DEF_SIZE = 8.5
HEADER_SIZE = 9.0
PAD = 12.0
SWATCH = 12.0
COL_W = 240.0  # one level entry, including swatch
COL_GAP = 18.0
ROW_GAP = 8.0
GAP_TO_DIAGRAM = 28.0
MUTED = QColor("#607d8b")


@dataclass(frozen=True)
class LegendEntry:
    color: str
    name: str
    definition: str = ""
    eliminated: bool = False


@dataclass
class LegendData:
    levels: list[LegendEntry] = field(default_factory=list)
    statuses: list[LegendEntry] = field(default_factory=list)  # empty: no status key


class LegendItem(QGraphicsItem):
    def __init__(self, data: LegendData, columns: int) -> None:
        super().__init__()
        self.data = data
        self.columns = max(1, min(columns, len(data.levels) or 1))
        self.setZValue(2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._layout()

    # ----- layout --------------------------------------------------------- #

    def _layout(self) -> None:
        name_fm = QFontMetricsF(make_font(NAME_SIZE, bold=True))
        def_fm = QFontMetricsF(make_font(DEF_SIZE))
        head_fm = QFontMetricsF(make_font(HEADER_SIZE, bold=True))
        text_w = COL_W - SWATCH - 8
        self.head_h = head_fm.height()

        # Level entries in a column-major grid.
        cells = []
        for e in self.data.levels:
            nh = name_fm.boundingRect(QRectF(0, 0, text_w, 1e5), TEXT_FLAGS, e.name).height()
            dh = (def_fm.boundingRect(QRectF(0, 0, text_w, 1e5), TEXT_FLAGS, e.definition).height()
                  if e.definition else 0.0)
            cells.append((nh, dh))
        rows = -(-len(cells) // self.columns) if cells else 0
        self.cells: list[tuple[LegendEntry, QRectF, float]] = []
        y0 = PAD + self.head_h + 6
        col_heights = [0.0] * self.columns
        for i, (entry, (nh, dh)) in enumerate(zip(self.data.levels, cells, strict=True)):
            col = i // rows if rows else 0
            x = PAD + col * (COL_W + COL_GAP)
            y = y0 + col_heights[col]
            h = nh + (dh + 2 if dh else 0)
            self.cells.append((entry, QRectF(x, y, COL_W, h), nh))
            col_heights[col] += h + ROW_GAP
        levels_bottom = y0 + max(col_heights, default=0.0)
        width = PAD * 2 + self.columns * COL_W + (self.columns - 1) * COL_GAP

        # Status key: one flowing row of dot + name chips.
        self.status_chips: list[tuple[LegendEntry, QRectF]] = []
        self.status_head_y = None
        bottom = levels_bottom
        if self.data.statuses:
            self.status_head_y = levels_bottom + 4
            x, y = PAD, self.status_head_y + self.head_h + 6
            line_h = name_fm.height()
            for e in self.data.statuses:
                w = 10 + 5 + def_fm.horizontalAdvance(e.name) + 14
                if x + w > width - PAD and x > PAD:
                    x, y = PAD, y + line_h + 4
                self.status_chips.append((e, QRectF(x, y, w, line_h)))
                x += w
            bottom = y + line_h
        self.rect = QRectF(0, 0, width, bottom + PAD)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-1, -1, 1, 1)

    # ----- painting ------------------------------------------------------- #

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#cfd8dc"), 1))
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawRoundedRect(self.rect, 6, 6)

        painter.setFont(make_font(HEADER_SIZE, bold=True))
        painter.setPen(MUTED)
        painter.drawText(QPointF(PAD, PAD + self.head_h * 0.8), "LEVELS")
        for entry, r, name_h in self.cells:
            sw = QRectF(r.left(), r.top() + (name_h - SWATCH) / 2, SWATCH, SWATCH)
            color = QColor(entry.color)
            if entry.eliminated:
                color.setAlphaF(0.55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(sw, 3, 3)
            tx = r.left() + SWATCH + 8
            painter.setFont(make_font(NAME_SIZE, bold=True, strike=entry.eliminated))
            painter.setPen(QColor(entry.color).darker(115))
            painter.drawText(QRectF(tx, r.top(), r.width() - SWATCH - 8, name_h), TEXT_FLAGS,
                             entry.name)
            if entry.definition:
                painter.setFont(make_font(DEF_SIZE))
                painter.setPen(MUTED)
                painter.drawText(QRectF(tx, r.top() + name_h + 2, r.width() - SWATCH - 8,
                                        r.height() - name_h), TEXT_FLAGS, entry.definition)

        if self.status_head_y is not None:
            painter.setFont(make_font(HEADER_SIZE, bold=True))
            painter.setPen(MUTED)
            painter.drawText(QPointF(PAD, self.status_head_y + self.head_h * 0.8), "STATUS")
            painter.setFont(make_font(DEF_SIZE))
            for entry, r in self.status_chips:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(entry.color))
                painter.drawEllipse(QRectF(r.left(), r.center().y() - 5, 10, 10))
                painter.setPen(QColor("#37474f"))
                painter.drawText(QRectF(r.left() + 15, r.top(), r.width() - 15, r.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 entry.name)


def place_legend(data: LegendData, position: str, diagram_bounds: QRectF) -> LegendItem:
    """Create a legend positioned next to the diagram (FR-3.5)."""
    if position == "right":
        item = LegendItem(data, columns=1)
        item.setPos(diagram_bounds.right() + GAP_TO_DIAGRAM, diagram_bounds.top())
        return item
    fit = int((max(diagram_bounds.width(), COL_W) - 2 * PAD + COL_GAP) // (COL_W + COL_GAP))
    item = LegendItem(data, columns=max(1, fit))
    x = (diagram_bounds.left() if position == "bottom-left"
         else diagram_bounds.right() - item.rect.width())
    item.setPos(x, diagram_bounds.bottom() + GAP_TO_DIAGRAM)
    return item
