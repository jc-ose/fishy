"""Diagram export (FR-8.1 – 8.4): PNG, SVG, PDF and the clipboard.

Exports are drawn from a dedicated off-screen scene, never the one on screen, so
they never pick up the selection outline, the current zoom, or the UI theme. The
scene holds the diagram, the legend (FR-3.5) and an optional title block with the
project name, problem statement, date and author (FR-8.2). It can also be laid out
fully expanded, ignoring collapsed bones (FR-8.3).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from fishy.layout import PRESETS, MeasureFn, layout_fishbone
from fishy.views.diagram_view import TEXT_FLAGS, DiagramScene, make_font
from fishy.views.legend import LegendData, LegendEntry

if TYPE_CHECKING:
    from fishy.document import ProjectDocument

MARGIN = 30.0  # around everything, in scene units
MAX_PNG_SIDE = 16000  # px; larger images are refused by many viewers
# Diagram fonts are sized in points, which Qt converts using the output device's DPI.
# Vector outputs get the screen's logical DPI so text keeps the size it was laid out at
# (PDF defaults to 1200 dpi and SVG to 72, which made text huge or tiny).
LOGICAL_DPI = 96

PAGE_SIZES: dict[str, QPageSize.PageSizeId] = {
    "Letter": QPageSize.PageSizeId.Letter,
    "Legal": QPageSize.PageSizeId.Legal,
    "Tabloid (11×17)": QPageSize.PageSizeId.Tabloid,
    "A4": QPageSize.PageSizeId.A4,
    "A3": QPageSize.PageSizeId.A3,
}
ORIENTATIONS = ("auto", "landscape", "portrait")


@dataclass
class DiagramExportOptions:
    title_block: bool = True
    title: str = ""  # defaults to the project name
    show_date: bool = True
    author: str = ""  # shown in the title block if set
    legend: bool = True
    status: bool = True  # status dots + status key + category progress rings
    expanded: bool = False  # ignore collapsed bones (FR-8.3)
    date_text: str = ""  # override for tests; defaults to today


# =========================================================================== #
# Title block (FR-8.2)
# =========================================================================== #

TITLE_SIZE = 15.0
PROBLEM_SIZE = 10.5
META_SIZE = 9.0
TITLE_GAP = 22.0  # between the title block and the diagram
INK = QColor("#263238")
SUBTLE = QColor("#607d8b")
RULE = QColor("#cfd8dc")


class TitleBlockItem(QGraphicsItem):
    """Project name and problem statement on the left, date and author on the right."""

    def __init__(self, title: str, problem: str, meta: list[str], width: float) -> None:
        super().__init__()
        self.title, self.problem, self.meta = title, problem, meta
        self.title_font = make_font(TITLE_SIZE, bold=True)
        self.problem_font = make_font(PROBLEM_SIZE)
        self.meta_font = make_font(META_SIZE)
        meta_fm = QFontMetricsF(self.meta_font)
        self.meta_w = max((meta_fm.horizontalAdvance(m) for m in meta), default=0.0)
        self.width = max(width, 420.0)
        text_w = self.width - (self.meta_w + 24 if meta else 0)
        tf = QFontMetricsF(self.title_font)
        pf = QFontMetricsF(self.problem_font)
        self.title_rect = tf.boundingRect(QRectF(0, 0, text_w, 1e6), TEXT_FLAGS, title)
        self.problem_rect = (pf.boundingRect(QRectF(0, 0, text_w, 1e6), TEXT_FLAGS, problem)
                             if problem else QRectF())
        self.text_w = text_w
        meta_h = meta_fm.lineSpacing() * len(meta)
        body_h = self.title_rect.height() + (self.problem_rect.height() + 4 if problem else 0)
        self.height = max(body_h, meta_h) + 10  # + space for the rule

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height + 1)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget | None = None) -> None:
        painter.setPen(INK)
        painter.setFont(self.title_font)
        painter.drawText(QRectF(0, 0, self.text_w, self.title_rect.height()), TEXT_FLAGS,
                         self.title)
        if self.problem:
            painter.setFont(self.problem_font)
            painter.setPen(SUBTLE)
            y = self.title_rect.height() + 4
            painter.drawText(QRectF(0, y, self.text_w, self.problem_rect.height()), TEXT_FLAGS,
                             self.problem)
        if self.meta:
            painter.setFont(self.meta_font)
            painter.setPen(SUBTLE)
            line = QFontMetricsF(self.meta_font).lineSpacing()
            for i, text in enumerate(self.meta):
                painter.drawText(QRectF(self.width - self.meta_w, i * line, self.meta_w, line),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 text)
        painter.setPen(QPen(RULE, 1))
        painter.drawLine(QPointF(0, self.height), QPointF(self.width, self.height))


# =========================================================================== #
# Building the export scene
# =========================================================================== #


def legend_for(doc: ProjectDocument, status: bool) -> LegendData:
    levels = [LegendEntry(lv.color, lv.name, lv.definition, lv.eliminated)
              for lv in doc.ordered_levels()]
    statuses = [LegendEntry(st.color, st.name) for st in doc.ordered_statuses()] if status else []
    return LegendData(levels, statuses)


def default_options(doc: ProjectDocument) -> DiagramExportOptions:
    """Start from what the project shows on screen (FR-3.5: exports match everywhere)."""
    p = doc.project
    users = doc.repo.users()
    me = users.get(doc.repo.user_id)
    return DiagramExportOptions(title=p.name, legend=p.show_legend, status=p.show_status,
                                author=me.display_name if me else "")


def build_scene(doc: ProjectDocument, measure: MeasureFn,
                opts: DiagramExportOptions) -> DiagramScene:
    """A fresh scene with exactly what the export should contain."""
    p = doc.project
    style = replace(PRESETS.get(p.style_preset, PRESETS["Default"]), show_status=opts.status)
    diagram = layout_fishbone(p.problem_statement or p.name,
                              doc.layout_nodes(expanded=opts.expanded), style, measure)
    scene = DiagramScene()
    scene.set_diagram(diagram, legend_for(doc, opts.status) if opts.legend else None,
                      p.legend_position)
    content = scene.sceneRect().marginsRemoved(QMarginsF(30, 30, 30, 30))
    if opts.title_block:
        meta = []
        if opts.show_date:
            meta.append(opts.date_text or date.today().strftime("%d %b %Y").lstrip("0"))
        if opts.author:
            meta.append(opts.author)
        block = TitleBlockItem(opts.title or p.name, p.problem_statement, meta, content.width())
        block.setPos(content.left(), content.top() - TITLE_GAP - block.height)
        scene.addItem(block)
        scene.title_block = block  # type: ignore[attr-defined]
        content = content.united(block.sceneBoundingRect())
    scene.setSceneRect(content.marginsAdded(QMarginsF(MARGIN, MARGIN, MARGIN, MARGIN)))
    return scene


# =========================================================================== #
# Rendering
# =========================================================================== #


def render_into(scene: DiagramScene, painter: QPainter, target: QRectF) -> None:
    """Draw the whole scene scaled to fit `target`, centered."""
    painter.setRenderHints(QPainter.RenderHint.Antialiasing
                           | QPainter.RenderHint.TextAntialiasing)
    src = scene.sceneRect()
    k = min(target.width() / src.width(), target.height() / src.height())
    fitted = QRectF(0, 0, src.width() * k, src.height() * k)
    fitted.moveCenter(target.center())  # centered on the page, both ways
    scene.render(painter, fitted, src, Qt.AspectRatioMode.KeepAspectRatio)


def clamp_scale(scene: DiagramScene, scale: float) -> float:
    """The largest scale ≤ `scale` that keeps the PNG within MAX_PNG_SIDE."""
    size = scene.sceneRect().size()
    return max(0.1, min(scale, MAX_PNG_SIDE / max(size.width(), size.height(), 1.0)))


def png_size(scene: DiagramScene, scale: float) -> tuple[int, int]:
    s = clamp_scale(scene, scale)
    size = scene.sceneRect().size() * s
    return round(size.width()), round(size.height())


def render_image(scene: DiagramScene, scale: float = 2.0) -> QImage:
    w, h = png_size(scene, scale)
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    render_into(scene, painter, QRectF(image.rect()))
    painter.end()
    # Metadata only, set after painting: Qt sizes point-based fonts by the device's
    # DPI, so setting it first would enlarge the text. 96 dpi at 1× means a 2× export
    # prints at the diagram's natural size.
    dpm = round(96 * clamp_scale(scene, scale) / 0.0254)
    image.setDotsPerMeterX(dpm)
    image.setDotsPerMeterY(dpm)
    return image


def export_png(scene: DiagramScene, path: str | Path, scale: float = 2.0) -> None:
    if not render_image(scene, scale).save(str(path), "PNG"):
        raise OSError(f"Could not write {path}")


def export_svg(scene: DiagramScene, path: str | Path, title: str = "Fishbone diagram") -> None:
    src = scene.sceneRect()
    gen = QSvgGenerator()
    gen.setFileName(str(path))
    gen.setSize(src.size().toSize())
    gen.setViewBox(QRectF(QPointF(0, 0), src.size()))
    gen.setResolution(LOGICAL_DPI)
    gen.setTitle(title)
    gen.setDescription("Exported from fishy")
    painter = QPainter(gen)
    painter.fillRect(QRectF(QPointF(0, 0), src.size()), QColor("white"))
    render_into(scene, painter, QRectF(QPointF(0, 0), src.size()))
    painter.end()


def resolve_orientation(scene: DiagramScene, orientation: str) -> QPageLayout.Orientation:
    if orientation == "auto":
        r = scene.sceneRect()
        orientation = "landscape" if r.width() >= r.height() else "portrait"
    return (QPageLayout.Orientation.Landscape if orientation == "landscape"
            else QPageLayout.Orientation.Portrait)


def export_pdf(scene: DiagramScene, path: str | Path, page: str = "Letter",
               orientation: str = "auto", margin_mm: float = 10.0,
               title: str = "Fishbone diagram") -> None:
    """Vector PDF, one page, diagram scaled to fit and centered (FR-8.1)."""
    writer = QPdfWriter(str(path))
    writer.setResolution(LOGICAL_DPI)  # still vector; see LOGICAL_DPI
    writer.setTitle(title)
    writer.setCreator("fishy")
    writer.setPageLayout(QPageLayout(QPageSize(PAGE_SIZES.get(page, PAGE_SIZES["Letter"])),
                                     resolve_orientation(scene, orientation),
                                     QMarginsF(margin_mm, margin_mm, margin_mm, margin_mm),
                                     QPageLayout.Unit.Millimeter))
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Could not write {path}")
    render_into(scene, painter, QRectF(QPointF(0, 0), QSizeF(writer.width(), writer.height())))
    painter.end()


def export_file(scene: DiagramScene, path: str | Path, *, scale: float = 2.0,
                page: str = "Letter", orientation: str = "auto",
                title: str = "Fishbone diagram") -> None:
    """Pick the format from the file extension."""
    ext = Path(path).suffix.lower()
    if ext == ".png":
        export_png(scene, path, scale)
    elif ext == ".svg":
        export_svg(scene, path, title)
    elif ext == ".pdf":
        export_pdf(scene, path, page, orientation, title=title)
    else:
        raise ValueError(f"Unsupported export format: {ext or '(none)'}")
