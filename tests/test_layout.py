"""Geometry tests for the pure-Python fishbone layout (no Qt needed)."""

from __future__ import annotations

import itertools
import time

import pytest

from fishy.layout import (
    COMPACT,
    DEFAULT,
    Diagram,
    LabelKind,
    LayoutNode,
    LineKind,
    approx_measure,
    assign_numbers,
    layout_fishbone,
)
from fishy.samples import SAMPLES, stress, typical

STYLES = [DEFAULT, COMPACT]
EPS = 0.5


# --------------------------------------------------------------------------- #
# geometry helpers (shared with the Qt tests)
# --------------------------------------------------------------------------- #


def _seg_hits_rect(x1, y1, x2, y2, r) -> bool:
    """Does segment (x1,y1)-(x2,y2) pass through rect r shrunk by EPS?"""
    rx0, ry0, rx1, ry1 = r.x + EPS, r.y + EPS, r.right - EPS, r.bottom - EPS
    if rx0 >= rx1 or ry0 >= ry1:
        return False
    t0, t1 = 0.0, 1.0
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - rx0), (dx, rx1 - x1), (-dy, y1 - ry0), (dy, ry1 - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _cross(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segments_cross(a, b) -> bool:
    """Proper crossing: interiors intersect. Attachments (touching) are allowed."""
    d1 = _cross(b.x1, b.y1, b.x2, b.y2, a.x1, a.y1)
    d2 = _cross(b.x1, b.y1, b.x2, b.y2, a.x2, a.y2)
    d3 = _cross(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1)
    d4 = _cross(a.x1, a.y1, a.x2, a.y2, b.x2, b.y2)
    tol = 1e-6 * max(1.0, abs(d1), abs(d2), abs(d3), abs(d4))
    return (d1 > tol and d2 < -tol or d1 < -tol and d2 > tol) and (
        d3 > tol and d4 < -tol or d3 < -tol and d4 > tol
    )


def assert_clean(d: Diagram) -> None:
    """No label overlaps, no line through a label, no line crossings."""
    for a, b in itertools.combinations(d.labels, 2):
        assert not a.rect.intersects(b.rect, tol=EPS), f"labels overlap: {a.text!r} / {b.text!r}"
    for ln in d.lines:
        for lab in d.labels:
            assert not _seg_hits_rect(ln.x1, ln.y1, ln.x2, ln.y2, lab.rect), (
                f"{ln.kind.value} line of bone {ln.bone_id} crosses label {lab.text!r}"
            )
    for a, b in itertools.combinations(d.lines, 2):
        assert not _segments_cross(a, b), f"lines of bones {a.bone_id} and {b.bone_id} cross"


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


def test_numbering_is_section_style():
    cats = [LayoutNode(1, "A", [LayoutNode(2, "a", [LayoutNode(3, "x")]), LayoutNode(4, "b")]),
            LayoutNode(5, "B")]
    assign_numbers(cats)
    assert [cats[0].number, cats[0].children[0].number, cats[0].children[0].children[0].number,
            cats[0].children[1].number, cats[1].number] == ["1", "1.1", "1.1.1", "1.2", "2"]


@pytest.mark.parametrize("style", STYLES, ids=lambda s: s.name)
@pytest.mark.parametrize("sample", list(SAMPLES))
def test_samples_have_no_collisions(sample, style):
    problem, cats = SAMPLES[sample]()
    assert_clean(layout_fishbone(problem, cats, style, approx_measure))


@pytest.mark.parametrize("seed", range(10))
def test_random_trees_have_no_collisions(seed):
    problem, cats = stress(n_bones=60, n_categories=6, seed=seed)
    assert_clean(layout_fishbone(problem, cats, DEFAULT, approx_measure))


def test_every_visible_bone_has_one_label_and_one_line():
    problem, cats = typical()
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    bone_labels = [lab.bone_id for lab in d.labels if lab.bone_id is not None]
    bone_lines = [ln.bone_id for ln in d.lines if ln.bone_id is not None]
    def count(nodes):
        return sum(1 + count(n.children) for n in nodes)

    assert len(bone_labels) == len(set(bone_labels)) == count(cats)
    assert sorted(bone_labels) == sorted(bone_lines)
    assert sum(1 for lab in d.labels if lab.kind is LabelKind.HEAD) == 1
    assert sum(1 for ln in d.lines if ln.kind is LineKind.SPINE) == 1


def test_categories_alternate_top_and_bottom():
    problem, cats = typical()
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    for i, cat in enumerate(cats):
        rect = d.label_for(cat.id).rect
        if i % 2 == 0:
            assert rect.bottom < 0, f"category {cat.number} should be above the spine"
        else:
            assert rect.y > 0, f"category {cat.number} should be below the spine"


def test_causes_read_top_down_on_both_sides():
    """1.1 is above 1.2 on screen, whether the category is above or below the spine."""
    problem, cats = typical()
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    for cat in cats:
        ys = [d.label_for(c.id).rect.y for c in cat.children]
        assert ys == sorted(ys), f"category {cat.number} causes not top-down: {ys}"


def test_branches_on_horizontal_bones_read_left_to_right():
    """1.1.1 is left of 1.1.2 on a horizontal bone, above or below the spine."""
    problem, cats = typical()
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    checked = 0
    for cat in cats:
        for cause in cat.children:  # horizontal bones
            if len(cause.children) > 1:
                xs = [d.label_for(c.id).rect.x for c in cause.children]
                assert xs == sorted(xs), f"{cause.number} branches not left-to-right: {xs}"
                checked += 1
    assert checked >= 2


def test_head_is_right_of_everything():
    problem, cats = stress()
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    head = next(lab for lab in d.labels if lab.kind is LabelKind.HEAD)
    others = [lab for lab in d.labels if lab.kind is not LabelKind.HEAD]
    assert max(lab.rect.right for lab in others) < head.rect.x


def test_collapsed_bone_hides_children_and_shows_badge():
    problem, cats = typical()
    target = cats[0].children[0]  # 1.1, which has two children
    target.collapsed = True
    d = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    assert d.label_for(target.children[0].id) is None
    assert d.label_for(target.id).badge == "+2"
    assert_clean(d)


def test_depth_limit_collapses_deeper_bones():
    deep = LayoutNode(1, "cat", [LayoutNode(2, "d2", [LayoutNode(3, "d3", [
        LayoutNode(4, "d4", [LayoutNode(5, "d5", [LayoutNode(6, "d6")])])])])])
    assign_numbers([deep])
    d = layout_fishbone("p", [deep], DEFAULT, approx_measure)
    assert d.label_for(4) is not None and d.label_for(4).badge == "+2"
    assert d.label_for(5) is None


def test_compact_is_smaller_than_default():
    problem, cats = typical()
    a = layout_fishbone(problem, cats, DEFAULT, approx_measure).bounds
    b = layout_fishbone(problem, cats, COMPACT, approx_measure).bounds
    assert b.w * b.h < 0.8 * a.w * a.h


def test_empty_and_childless_diagrams():
    assert_clean(layout_fishbone("Only a problem", [], DEFAULT, approx_measure))
    cats = [LayoutNode(1, "People"), LayoutNode(2, "Machine")]
    assign_numbers(cats)
    assert_clean(layout_fishbone("", cats, DEFAULT, approx_measure))


def test_layout_is_deterministic():
    problem, cats = stress()
    a = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    b = layout_fishbone(problem, cats, DEFAULT, approx_measure)
    assert a.labels == b.labels and a.lines == b.lines


def test_performance_budget():
    """NFR-2: < 50 ms typical, < 100 ms at the 150-bone design limit (generous here)."""
    for build, budget_ms in ((typical, 50), (stress, 100)):
        problem, cats = build()
        layout_fishbone(problem, cats, DEFAULT, approx_measure)  # warm-up
        t0 = time.perf_counter()
        layout_fishbone(problem, cats, DEFAULT, approx_measure)
        assert (time.perf_counter() - t0) * 1000 < budget_ms
