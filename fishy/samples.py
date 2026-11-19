"""Sample investigations for the layout spike (demo and tests).

The level/status palettes and the roll-up here are placeholders for the real
domain layer (milestones 2 and 4); they exist so the spike draws realistic
colors.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from fishy.layout import LayoutNode, assign_numbers


@dataclass(frozen=True)
class Level:
    name: str
    color: str
    severity: int  # higher = more severe; drives roll-up (FR-3.7)


ROOT = Level("Root Cause", "#c62828", 4)
DIRECT = Level("Direct Cause", "#e65100", 3)
IMMEDIATE = Level("Immediate Cause", "#b8860b", 2)
POTENTIAL = Level("Potential Contributor", "#546e7a", 1)
RULED_OUT = Level("Ruled Out", "#9e9e9e", 0)
LEVELS = [ROOT, DIRECT, IMMEDIATE, POTENTIAL, RULED_OUT]

STATUSES = {
    "Not Started": "#b0bec5",
    "In Progress": "#1e88e5",
    "Blocked": "#e53935",
    "Under Review": "#8e24aa",
    "Complete": "#43a047",
}

_CATEGORY_COLOR = "#37474f"


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _node(ids: _Ids, title: str, level: Level = POTENTIAL, status: str = "Not Started",
          children: list[LayoutNode] | None = None) -> LayoutNode:
    node = LayoutNode(id=ids.next(), title=title, children=children or [],
                      color=level.color, status_color=STATUSES[status],
                      dimmed=level is RULED_OUT)
    node._level = level  # type: ignore[attr-defined]
    return node


def roll_up(categories: list[LayoutNode]) -> None:
    """Parents take the color of their most severe child (FR-3.7)."""

    def walk(node: LayoutNode) -> Level:
        level: Level = getattr(node, "_level", POTENTIAL)
        if node.children:
            child_levels = [walk(c) for c in node.children]
            if not node.pinned:
                level = max(child_levels, key=lambda lv: lv.severity)
        node.color = level.color
        node.dimmed = level is RULED_OUT
        node._level = level  # type: ignore[attr-defined]
        return level

    for cat in categories:
        walk(cat)
        cat.status_color = None  # categories show a summary instead (FR-3a.6)


def _finish(cats: list[LayoutNode]) -> list[LayoutNode]:
    roll_up(cats)
    for c in cats:
        if c.color == POTENTIAL.color:
            c.color = _CATEGORY_COLOR
    assign_numbers(cats)
    return cats


def typical() -> tuple[str, list[LayoutNode]]:
    """A realistic 26-bone investigation (the common case, FR-2.9)."""
    i = _Ids()
    n = lambda *a, **k: _node(i, *a, **k)  # noqa: E731
    cats = [
        n("People", children=[
            n("Operator unfamiliar with new SOP", DIRECT, "Complete", [
                n("Training record shows module skipped", ROOT, "Complete"),
                n("SOP rev C issued without briefing", IMMEDIATE, "Under Review"),
            ]),
            n("Shift handover incomplete", POTENTIAL, "In Progress"),
            n("Fatigue (12 h shift)", RULED_OUT, "Complete"),
        ]),
        n("Machine", children=[
            n("Seal press temperature drift", DIRECT, "In Progress", [
                n("Thermocouple out of calibration", POTENTIAL, "In Progress"),
                n("PID tuning changed in March", POTENTIAL, "Not Started"),
            ]),
            n("Worn die", RULED_OUT, "Complete"),
        ]),
        n("Method", children=[
            n("Dwell time not specified for new film", ROOT, "Under Review"),
            n("No first-article check after changeover", IMMEDIATE, "Blocked", [
                n("Inspection station moved", POTENTIAL, "Not Started"),
            ]),
        ]),
        n("Material", children=[
            n("New film supplier qualified in Q2", DIRECT, "In Progress", [
                n("Film thickness at low end of spec", POTENTIAL, "In Progress"),
                n("Coating differs from previous supplier", POTENTIAL, "Not Started"),
            ]),
            n("Humidity exposure in storage", RULED_OUT, "Complete"),
        ]),
        n("Measurement", children=[
            n("Seal strength test sampling too sparse", IMMEDIATE, "Under Review"),
            n("Gauge R&R not repeated", POTENTIAL, "Not Started"),
        ]),
        n("Environment", children=[
            n("Ambient temperature swing in summer", POTENTIAL, "In Progress"),
            n("HVAC maintenance overdue", POTENTIAL, "Blocked"),
        ]),
    ]
    return "Pouch seal failures increased 4× on Line 3", _finish(cats)


def small() -> tuple[str, list[LayoutNode]]:
    i = _Ids()
    n = lambda *a, **k: _node(i, *a, **k)  # noqa: E731
    cats = [
        n("Hardware", children=[n("Loose connector", ROOT, "Complete")]),
        n("Software", children=[n("Retry limit too low", DIRECT, "In Progress")]),
        n("Process", children=[]),
    ]
    return "Intermittent sensor dropouts", _finish(cats)


def deep() -> tuple[str, list[LayoutNode]]:
    """Nesting deeper than max_depth, to exercise collapse badges (FR-1.6)."""
    i = _Ids()
    n = lambda *a, **k: _node(i, *a, **k)  # noqa: E731

    def chain(remaining: int, level: int = 2) -> list[LayoutNode]:
        if remaining == 0:
            return []
        return [n(f"Why? level {level}", POTENTIAL, "In Progress", chain(remaining - 1, level + 1)),
                n("Side branch", POTENTIAL, "Not Started")]

    cats = [n("Process", children=chain(5)), n("Equipment", children=chain(4)),
            n("Inputs", children=chain(3))]
    return "Deep why-chain", _finish(cats)


def long_labels() -> tuple[str, list[LayoutNode]]:
    i = _Ids()
    n = lambda *a, **k: _node(i, *a, **k)  # noqa: E731
    long = ("Supplier changed the resin formulation without notifying purchasing, "
            "and incoming inspection did not catch the difference")
    cats = [
        n("Materials and incoming supply chain", children=[
            n(long, ROOT, "Complete", [n(long, POTENTIAL), n("Short one", POTENTIAL)]),
            n("Supercalifragilisticexpialidocious-unbroken-token-label", POTENTIAL),
        ]),
        n("Method", children=[n(long, DIRECT, "In Progress")]),
        n("A", children=[n("B", IMMEDIATE, "Blocked")]),
    ]
    return ("Customer complaints about cracked housings on the premium product line "
            "rose sharply after the spring model changeover"), _finish(cats)


def stress(n_bones: int = 150, n_categories: int = 8,
           seed: int = 7) -> tuple[str, list[LayoutNode]]:
    """A random tree at the design limit (FR-2.9)."""
    rng = random.Random(seed)
    i = _Ids()
    words = ("valve pump operator sensor gasket training torque supplier batch audit "
             "drift alarm firmware calibration schedule humidity handover spec").split()
    statuses = list(STATUSES)
    cats = [_node(i, f"Category {k + 1}") for k in range(n_categories)]
    pool: list[tuple[LayoutNode, int]] = [(c, 1) for c in cats]
    count = n_categories
    while count < n_bones:
        parent, depth = rng.choice(pool)
        if depth >= 4:
            continue
        title = " ".join(rng.choice(words) for _ in range(rng.randint(1, 6))).capitalize()
        child = _node(i, title, rng.choice(LEVELS), rng.choice(statuses))
        parent.children.append(child)
        pool.append((child, depth + 1))
        count += 1
    return f"Stress test: {n_bones} bones", _finish(cats)


SAMPLES = {
    "Typical (26 bones)": typical,
    "Small": small,
    "Deep nesting": deep,
    "Long labels": long_labels,
    "Stress (150 bones)": stress,
}
