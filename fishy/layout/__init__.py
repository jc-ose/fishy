"""Fishbone layout engine: pure Python, no Qt widget dependencies."""

from .fishbone_layout import MeasureFn, approx_measure, layout_fishbone
from .model import (
    Diagram,
    Label,
    LabelKind,
    LayoutNode,
    Line,
    LineKind,
    Rect,
    assign_numbers,
    count_descendants,
)
from .styles import COMPACT, DEFAULT, PRESETS, FishboneStyle

__all__ = [
    "COMPACT",
    "DEFAULT",
    "PRESETS",
    "Diagram",
    "FishboneStyle",
    "Label",
    "LabelKind",
    "LayoutNode",
    "Line",
    "LineKind",
    "MeasureFn",
    "Rect",
    "approx_measure",
    "assign_numbers",
    "count_descendants",
    "layout_fishbone",
]
