"""Automatic Ishikawa (fishbone) layout.

Pure Python, no Qt: `layout_fishbone(problem, categories, style, measure)`
returns a `Diagram` of positioned lines and labels.

Shape
-----
* A horizontal spine runs into the head (problem statement) on the right.
* Categories alternate above and below the spine. Each category is a *slanted*
  bone ending in a category box.
* Bones then alternate orientation with depth: causes on a slanted bone are
  horizontal; causes on a horizontal bone are slanted (parallel to the main
  bones), and so on.

Method
------
Everything is laid out for the *top* side in a y-up frame, then mirrored for
the bottom side and converted to Qt's y-down coordinates at the end.

The key trick is a shear transform, ``u = x + y·cot(angle)``, ``v = y``. In
(u, v) space every slanted bone becomes vertical and horizontal bones stay
horizontal, so the fishbone becomes an orthogonal tree. Each subtree keeps a
list of axis-aligned boxes in (u, v) space (one per line and per label, using
the bounding box of each sheared label). Siblings are packed by sliding a new
subtree along one axis until none of its boxes come within `sibling_gap` of the
boxes already placed. Because shearing is a bijection, disjoint boxes in (u, v)
space guarantee disjoint shapes in the final picture. Labels and lines
therefore never overlap, and subtrees still interlock tightly where their
shapes allow it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from .model import (
    Diagram,
    Label,
    LabelKind,
    LayoutNode,
    Line,
    LineKind,
    Rect,
    count_descendants,
)
from .styles import DEFAULT, FishboneStyle

# measure(text, max_width, font_size, bold) -> (width, height) of the wrapped text.
MeasureFn = Callable[[str, float, float, bool], tuple[float, float]]


def approx_measure(text: str, max_width: float, font_size: float,
                   bold: bool = False) -> tuple[float, float]:
    """Font-free text measurement used by tests and as a fallback.

    Greedy word wrap with an average glyph width of 0.56 em and 1.3 em lines.
    The Qt renderer passes a real `QFontMetricsF`-based function instead.
    """
    char_w = (0.60 if bold else 0.56) * font_size
    line_h = 1.3 * font_size
    max_chars = max(1, int(max_width // char_w))
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > max_chars:  # hard-break very long words
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    width = max(len(s) for s in lines) * char_w
    return width, len(lines) * line_h


# --------------------------------------------------------------------------- #
# Internal geometry (canonical frame: top side, y up, origin = attach point)
# --------------------------------------------------------------------------- #


@dataclass
class _Box:
    """Axis-aligned box in sheared (u, v) space."""

    u0: float
    u1: float
    v0: float
    v1: float

    def moved(self, du: float, dv: float) -> _Box:
        return _Box(self.u0 + du, self.u1 + du, self.v0 + dv, self.v1 + dv)


@dataclass
class _Line:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: LineKind
    node: LayoutNode | None


@dataclass
class _Label:
    kind: LabelKind
    node: LayoutNode | None
    x: float  # left
    y: float  # bottom (y-up frame)
    w: float
    h: float
    text: str
    font_size: float
    text_dx: float  # text box offset inside the label box
    text_dy: float
    text_w: float
    text_h: float
    dot: tuple[float, float, float] | None = None  # (dx, dy, size) inside box
    badge: str | None = None
    pin: tuple[float, float, float] | None = None  # (dx, dy, size) inside box
    ring: tuple[float, float, float] | None = None  # (dx, dy, size) inside box


@dataclass
class _Sub:
    lines: list[_Line] = field(default_factory=list)
    labels: list[_Label] = field(default_factory=list)
    boxes: list[_Box] = field(default_factory=list)

    def add(self, other: _Sub, du: float, dv: float, cot: float) -> None:
        """Add `other`, whose origin goes to sheared offset (du, dv)."""
        dx, dy = du - dv * cot, dv  # inverse shear of the offset
        for ln in other.lines:
            self.lines.append(
                _Line(ln.x1 + dx, ln.y1 + dy, ln.x2 + dx, ln.y2 + dy, ln.kind, ln.node)
            )
        for lab in other.labels:
            self.labels.append(_shift_label(lab, dx, dy))
        self.boxes.extend(b.moved(du, dv) for b in other.boxes)


def _shift_label(lab: _Label, dx: float, dy: float) -> _Label:
    return _Label(
        lab.kind, lab.node, lab.x + dx, lab.y + dy, lab.w, lab.h, lab.text,
        lab.font_size, lab.text_dx, lab.text_dy, lab.text_w, lab.text_h,
        lab.dot, lab.badge, lab.pin, lab.ring,
    )


class _Engine:
    def __init__(self, style: FishboneStyle, measure: MeasureFn) -> None:
        self.s = style
        self.measure = measure
        self.cot = 1.0 / math.tan(math.radians(style.angle_deg))

    # ----- helpers --------------------------------------------------------- #

    def label_box_sheared(self, x: float, y: float, w: float, h: float) -> _Box:
        c = self.cot
        return _Box(x + y * c, x + w + (y + h) * c, y, y + h)

    def line_box(self, x1: float, y1: float, x2: float, y2: float) -> _Box:
        c = self.cot
        ua, ub = x1 + y1 * c, x2 + y2 * c
        return _Box(min(ua, ub), max(ua, ub), min(y1, y2), max(y1, y2))

    def visible_children(self, node: LayoutNode, depth: int) -> list[LayoutNode]:
        if node.collapsed or depth >= self.s.max_depth:
            return []
        return node.children

    def make_label(self, node: LayoutNode, depth: int, kind: LabelKind) -> _Label:
        s = self.s
        if kind is LabelKind.CATEGORY:
            fs, max_w, pad = s.category_font_size, s.category_max_width, s.category_pad
        else:
            fs, max_w, pad = s.font_size, s.label_max_width, 0.0
        text = f"{node.number} {node.title}".strip()
        tw, th = self.measure(text, max_w, fs, kind is LabelKind.CATEGORY)

        hidden = 0 if self.visible_children(node, depth) else count_descendants(node)
        badge = f"+{hidden}" if hidden else None
        badge_w = 0.0
        if badge:
            badge_w = self.measure(badge, 1e6, fs * 0.85, True)[0] + 8.0 + s.status_gap

        # Leading markers, left to right: status dot | progress ring, then pin.
        lead = pad
        dot = ring = pin = None
        # Centre markers on the first text line (labels can wrap onto several lines).
        first_line = min(th, self.measure("Ag", 1e6, fs, kind is LabelKind.CATEGORY)[1])
        if s.show_status and node.status_color and kind is LabelKind.CAUSE:
            dot = (lead, pad + (first_line - s.status_dot) / 2, s.status_dot)
            lead += s.status_dot + s.status_gap
        if s.show_status and node.progress and node.progress[1] and kind is LabelKind.CATEGORY:
            ring = (lead, pad + (first_line - s.ring_size) / 2, s.ring_size)
            lead += s.ring_size + s.status_gap + 1
        if node.pinned and kind is LabelKind.CAUSE:
            pin = (lead, pad + (first_line - s.pin_size) / 2, s.pin_size)
            lead += s.pin_size + s.status_gap
        w = lead + tw + badge_w + pad
        h = th + 2 * pad
        return _Label(
            kind, node, 0.0, 0.0, w, h, text, fs,
            text_dx=lead, text_dy=pad, text_w=tw, text_h=th,
            dot=dot, badge=badge, pin=pin, ring=ring,
        )

    # ----- packing --------------------------------------------------------- #

    def pack_along_u(self, placed: list[_Box], new: list[_Box], start: float) -> float:
        """Largest du <= start so `new` (shifted by du) clears `placed`, moving left."""
        g = self.s.sibling_gap
        du = start
        for b in new:
            for a in placed:
                if b.v0 < a.v1 + g and a.v0 < b.v1 + g:
                    du = min(du, a.u0 - g - b.u1)
        return du

    def pack_along_v(self, placed: list[_Box], new: list[_Box], start: float) -> float:
        """Smallest dv >= start so `new` (shifted by dv) clears `placed`, moving up."""
        g = self.s.sibling_gap
        dv = start
        for b in new:
            for a in placed:
                if b.u0 < a.u1 + g and a.u0 < b.u1 + g:
                    dv = max(dv, a.v1 + g - b.v0)
        return dv

    # ----- bones ----------------------------------------------------------- #

    def slanted(self, node: LayoutNode, depth: int, top: bool) -> _Sub:
        """A bone running up-left from its base at the origin.

        Its children are horizontal bones attached along it, stacked upward
        (away from the base). Causes should read top-down on screen, so on the
        top side the *last* child goes nearest the base; the bottom side is a
        mirror image, so there the first child goes nearest the base.
        """
        s, c = self.s, self.cot
        kind = LabelKind.CATEGORY if depth == 1 else LabelKind.CAUSE
        sub = _Sub()

        children = self.visible_children(node, depth)
        if top:
            children = list(reversed(children))
        placed: list[_Box] = []
        cursor = s.first_attach
        for child in children:
            csub = self.horizontal(child, depth + 1, top)
            dv = self.pack_along_v(placed, csub.boxes, cursor - min(b.v0 for b in csub.boxes))
            sub.add(csub, 0.0, dv, c)
            placed.extend(b.moved(0.0, dv) for b in csub.boxes)
            cursor = dv + s.first_attach

        lab = self.make_label(node, depth, kind)
        # Label sits above the tip, centred on it. In sheared space its u-range
        # does not depend on the bone length, so the length can be solved for.
        lg = s.label_gap
        lab_u0 = -lab.w / 2 + lg * c
        lab_u1 = lab.w / 2 + (lg + lab.h) * c
        min_len = s.min_main_len if depth == 1 else s.min_branch_len
        length = max(min_len, cursor + s.first_attach)
        for b in placed:
            if b.u0 < lab_u1 + s.sibling_gap and lab_u0 < b.u1 + s.sibling_gap:
                length = max(length, b.v1 + s.sibling_gap - lg)

        tip_x, tip_y = -length * c, length
        sub.lines.append(_Line(0.0, 0.0, tip_x, tip_y,
                               LineKind.MAIN if depth == 1 else LineKind.BRANCH, node))
        sub.boxes.append(self.line_box(0.0, 0.0, tip_x, tip_y))

        lab.x, lab.y = tip_x - lab.w / 2, tip_y + lg
        sub.labels.append(lab)
        sub.boxes.append(self.label_box_sheared(lab.x, lab.y, lab.w, lab.h))
        return sub

    def horizontal(self, node: LayoutNode, depth: int, top: bool) -> _Sub:
        """A bone running left from its attach point at the origin.

        Its children are slanted bones rising from points along it, packed
        right-to-left; its label sits just past its left (free) end.
        """
        s, c = self.s, self.cot
        sub = _Sub()

        # Children are packed outward from the attach point (right to left). To read
        # left to right (x.1 left of x.2), the last child goes nearest the attach point.
        placed: list[_Box] = []
        bases: list[float] = []
        for child in reversed(self.visible_children(node, depth)):
            csub = self.slanted(child, depth + 1, top)
            start = (bases[-1] if bases else 0.0) - s.branch_inset
            start = min(start, -s.branch_inset - max(b.u1 for b in csub.boxes))
            du = self.pack_along_u(placed, csub.boxes, start)
            sub.add(csub, du, 0.0, c)
            placed.extend(b.moved(du, 0.0) for b in csub.boxes)
            bases.append(du)

        lab = self.make_label(node, depth, LabelKind.CAUSE)
        lg, half = s.label_gap, lab.h / 2
        tail = -s.min_branch_len
        if bases:
            tail = min(tail, bases[-1] - s.branch_inset)
        # Push the tail left until the label clears every child box it could touch.
        for b in placed:
            if b.v0 < half + s.sibling_gap and -half < b.v1 + s.sibling_gap:
                tail = min(tail, b.u0 - s.sibling_gap + lg - half * c)

        sub.lines.append(_Line(0.0, 0.0, tail, 0.0, LineKind.BRANCH, node))
        sub.boxes.append(self.line_box(0.0, 0.0, tail, 0.0))

        lab.x, lab.y = tail - lg - lab.w, -half
        sub.labels.append(lab)
        sub.boxes.append(self.label_box_sheared(lab.x, lab.y, lab.w, lab.h))
        return sub

    # ----- whole diagram --------------------------------------------------- #

    def run(self, problem: str, categories: list[LayoutNode]) -> Diagram:
        s = self.s
        diagram = Diagram()

        # Head box, left edge at x = 0, centred on the spine.
        hw, hh = self.measure(problem or "Problem statement", s.head_max_width,
                              s.head_font_size, True)
        head = Rect(0.0, -(hh / 2 + s.head_pad), hw + 2 * s.head_pad, hh + 2 * s.head_pad)
        diagram.labels.append(Label(
            bone_id=None, kind=LabelKind.HEAD, rect=head,
            text_rect=Rect(head.x + s.head_pad, head.y + s.head_pad, hw, hh),
            text=problem or "Problem statement", font_size=s.head_font_size, color="#263238",
        ))

        # Alternate sides; category 1 ends up top-left. Pack right-to-left.
        sides: dict[bool, list[LayoutNode]] = {True: [], False: []}
        for i, cat in enumerate(categories):
            sides[i % 2 == 0].append(cat)

        leftmost = -s.head_gap
        for top, cats in sides.items():
            placed: list[_Box] = []
            cursor = -s.head_gap
            for cat in reversed(cats):
                csub = self.slanted(cat, 1, top)
                start = min(cursor, -s.head_gap - max(b.u1 for b in csub.boxes))
                du = self.pack_along_u(placed, csub.boxes, start)
                placed.extend(b.moved(du, 0.0) for b in csub.boxes)
                cursor = du - s.sibling_gap
                self.emit(diagram, csub, du, top)
            if placed:
                # Real-space left edge: u = x + y·cot, so x >= u - v·cot.
                leftmost = min(leftmost, min(b.u0 - b.v1 * self.cot for b in placed))

        spine_x0 = min(leftmost, -s.head_gap) - s.spine_lead
        diagram.lines.insert(0, Line(spine_x0, 0.0, 0.0, 0.0, LineKind.SPINE, None, "#263238"))

        bounds = head
        for lab in diagram.labels:
            bounds = bounds.united(lab.rect)
        for ln in diagram.lines:
            bounds = bounds.united(Rect(min(ln.x1, ln.x2), min(ln.y1, ln.y2),
                                        abs(ln.x2 - ln.x1), abs(ln.y2 - ln.y1)))
        diagram.bounds = bounds
        return diagram

    def emit(self, diagram: Diagram, sub: _Sub, du: float, top: bool) -> None:
        """Convert a canonical category subtree to Qt coordinates and append it."""
        sign = -1.0 if top else 1.0  # top side: y-up -> y-down flip; bottom: mirror
        for ln in sub.lines:
            n = ln.node
            diagram.lines.append(Line(
                ln.x1 + du, sign * ln.y1, ln.x2 + du, sign * ln.y2, ln.kind,
                n.id if n else None, n.color if n else "#263238",
                dimmed=bool(n and n.dimmed),
            ))
        for lab in sub.labels:
            n = lab.node
            y_low, y_high = lab.y, lab.y + lab.h
            top_y = -y_high if top else y_low
            rect = Rect(lab.x + du, top_y, lab.w, lab.h)
            text_rect = Rect(rect.x + lab.text_dx, rect.y + lab.text_dy, lab.text_w, lab.text_h)
            def inside(marker, box=rect):
                if not marker:
                    return None
                mx, my, size = marker
                return Rect(box.x + mx, box.y + my, size, size)
            diagram.labels.append(Label(
                bone_id=n.id if n else None, kind=lab.kind, rect=rect, text_rect=text_rect,
                text=lab.text, font_size=lab.font_size,
                color=n.color if n else "#263238",
                status_color=n.status_color if n else None,
                status_dot=inside(lab.dot), badge=lab.badge,
                dimmed=bool(n and n.dimmed), pinned=bool(n and n.pinned),
                pin_mark=inside(lab.pin),
                progress=n.progress if n and lab.ring else None,
                progress_rect=inside(lab.ring),
            ))


def layout_fishbone(
    problem: str,
    categories: list[LayoutNode],
    style: FishboneStyle = DEFAULT,
    measure: MeasureFn = approx_measure,
) -> Diagram:
    """Lay out a fishbone diagram.

    `categories` are the top-level bones, in display order, with `number`
    already assigned (see `model.assign_numbers`). Returns a `Diagram` in
    Qt scene coordinates: spine on y = 0, head box starting at x = 0.
    """
    return _Engine(style, measure).run(problem, categories)
