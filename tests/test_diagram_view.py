"""Qt-side tests: real font metrics, selection sync, export."""

from __future__ import annotations

import pytest

from fishy.layout import COMPACT, DEFAULT, layout_fishbone
from fishy.samples import SAMPLES, typical
from fishy.views.diagram_view import DiagramScene, DiagramView, qt_measure

from .test_layout import assert_clean


@pytest.fixture
def view(qtbot):
    scene = DiagramScene()
    v = DiagramView(scene)
    qtbot.addWidget(v)
    return v


@pytest.mark.parametrize("style", [DEFAULT, COMPACT], ids=lambda s: s.name)
@pytest.mark.parametrize("sample", list(SAMPLES))
def test_no_collisions_with_real_fonts(qapp, sample, style):
    problem, cats = SAMPLES[sample]()
    assert_clean(layout_fishbone(problem, cats, style, qt_measure()))


def test_clicking_a_bone_reports_its_id(view, qtbot):
    problem, cats = typical()
    view.scene().set_diagram(layout_fishbone(problem, cats, DEFAULT, qt_measure()))
    bone = cats[2].children[1].id
    with qtbot.waitSignal(view.scene().boneSelected) as sig:
        view.scene()._labels[bone].setSelected(True)
    assert sig.args == [bone]


def test_selection_survives_relayout(view):
    problem, cats = typical()
    scene = view.scene()
    scene.set_diagram(layout_fishbone(problem, cats, DEFAULT, qt_measure()))
    bone = cats[0].children[0].id
    scene.select_bone(bone)
    scene.set_diagram(layout_fishbone(problem, cats, COMPACT, qt_measure()))
    assert scene.selected_bone() == bone


def test_exports_write_files(view, tmp_path):
    problem, cats = typical()
    view.scene().set_diagram(layout_fishbone(problem, cats, DEFAULT, qt_measure()))
    for ext in ("png", "svg", "pdf"):
        path = tmp_path / f"out.{ext}"
        getattr(view, f"export_{ext}")(str(path))
        assert path.stat().st_size > 1000, ext
