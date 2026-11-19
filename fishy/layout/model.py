"""Input and output types for the fishbone layout engine.

These are deliberately independent of Qt and of the storage layer: the engine
takes a plain tree of `LayoutNode`s and returns a `Diagram` of positioned
primitives (lines and label boxes) that any renderer can draw.

Coordinates in the output use the Qt convention: x grows to the right, y grows
downward. The spine lies on y = 0, top-side categories have negative y.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class LayoutNode:
    """One bone in the tree handed to the layout engine.

    `id` is the stable bone ID; `number` is the display number ("1.2.3"), which
    the caller computes (see `assign_numbers`). Colors are CSS-style hex
    strings; the engine passes them through untouched.
    """

    id: int
    title: str
    children: list[LayoutNode] = field(default_factory=list)
    number: str = ""
    color: str = "#455a64"
    status_color: str | None = None
    collapsed: bool = False
    dimmed: bool = False  # e.g. Ruled Out
    pinned: bool = False  # level overrides roll-up: drawn with a pin marker
    progress: tuple[int, int] | None = None  # categories: (complete, total) causes


class LabelKind(Enum):
    HEAD = "head"
    CATEGORY = "category"
    CAUSE = "cause"


class LineKind(Enum):
    SPINE = "spine"
    MAIN = "main"  # category bone, spine -> category box
    BRANCH = "branch"  # any cause bone


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def intersects(self, other: Rect, tol: float = 0.0) -> bool:
        return not (
            self.right <= other.x + tol
            or other.right <= self.x + tol
            or self.bottom <= other.y + tol
            or other.bottom <= self.y + tol
        )

    def united(self, other: Rect) -> Rect:
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.right, other.right)
        y1 = max(self.bottom, other.bottom)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def flipped_y(self) -> Rect:
        return Rect(self.x, -self.bottom, self.w, self.h)

    def translated(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: LineKind
    bone_id: int | None
    color: str
    dimmed: bool = False


@dataclass(frozen=True)
class Label:
    bone_id: int | None
    kind: LabelKind
    rect: Rect  # full box, including status dot and badge
    text_rect: Rect  # where the text itself is drawn
    text: str
    font_size: float
    color: str
    status_color: str | None = None
    status_dot: Rect | None = None
    badge: str | None = None  # e.g. "+3" when collapsed
    dimmed: bool = False
    pinned: bool = False
    pin_mark: Rect | None = None  # where to draw the pin marker
    progress: tuple[int, int] | None = None
    progress_rect: Rect | None = None  # where to draw the category progress ring


@dataclass
class Diagram:
    lines: list[Line] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    bounds: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))

    def label_for(self, bone_id: int) -> Label | None:
        for lab in self.labels:
            if lab.bone_id == bone_id:
                return lab
        return None


def assign_numbers(categories: list[LayoutNode]) -> None:
    """Fill in section-style numbers (1, 1.1, 1.1.2, ...) from tree position."""

    def walk(nodes: list[LayoutNode], prefix: str) -> None:
        for i, node in enumerate(nodes, start=1):
            node.number = f"{prefix}{i}"
            walk(node.children, f"{node.number}.")

    walk(categories, "")


def count_descendants(node: LayoutNode) -> int:
    return sum(1 + count_descendants(c) for c in node.children)
