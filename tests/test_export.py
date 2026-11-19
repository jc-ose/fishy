"""Milestone 6: diagram export (PNG/SVG/PDF/clipboard), title block, outline, JSON."""

from __future__ import annotations

import base64
import json

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication, QImage

from fishy.app import MainWindow, create_sample_project
from fishy.export import (
    DiagramExportOptions,
    build_scene,
    default_options,
    export_file,
    export_pdf,
    export_png,
    export_svg,
    outline_text,
    png_size,
    project_dict,
    render_image,
    write_json,
)
from fishy.export.diagram import MAX_PNG_SIDE, TitleBlockItem
from fishy.views.diagram_view import LabelItem, qt_measure
from fishy.views.export_dialog import ExportDialog, safe_filename


@pytest.fixture
def doc(qapp, tmp_path):
    d = create_sample_project(tmp_path / "s.fishy")
    yield d
    d.close()


@pytest.fixture
def measure(qapp):
    return qt_measure()


def labels(scene) -> set[int]:
    return {i.label.bone_id for i in scene.items()
            if isinstance(i, LabelItem) and i.label.bone_id is not None}


def test_title_block_sits_above_the_diagram(doc, measure):
    opts = DiagramExportOptions(title="Line 3 RCA", author="Pat", date_text="1 Dec 2026")
    scene = build_scene(doc, measure, opts)
    block = scene.title_block
    assert block.title == "Line 3 RCA"
    assert block.problem == doc.project.problem_statement
    assert block.meta == ["1 Dec 2026", "Pat"]
    others = [i for i in scene.items() if i is not block and i.parentItem() is None]
    top_of_diagram = min(i.sceneBoundingRect().top() for i in others)
    assert block.sceneBoundingRect().bottom() < top_of_diagram
    assert scene.sceneRect().contains(block.sceneBoundingRect())


def test_title_block_and_legend_are_optional(doc, measure):
    bare = build_scene(doc, measure, DiagramExportOptions(title_block=False, legend=False))
    full = build_scene(doc, measure, DiagramExportOptions())
    assert not any(isinstance(i, TitleBlockItem) for i in bare.items())
    assert bare.legend_item is None and full.legend_item is not None
    assert bare.sceneRect().height() < full.sceneRect().height()


def test_status_toggle_controls_dots_and_key(doc, measure):
    on = build_scene(doc, measure, DiagramExportOptions(status=True))
    off = build_scene(doc, measure, DiagramExportOptions(status=False))
    assert on.legend_item.data.statuses and not off.legend_item.data.statuses
    assert any(i.label.status_dot for i in on.items() if isinstance(i, LabelItem))
    assert not any(i.label.status_dot for i in off.items() if isinstance(i, LabelItem))


def test_defaults_follow_project_settings(doc):
    doc.set_project_fields(show_legend=False, show_status=False)
    opts = default_options(doc)
    assert opts.title == doc.project.name
    assert not opts.legend and not opts.status
    assert opts.author  # the current user


def test_fully_expanded_includes_collapsed_bones(doc, measure):
    parent = next(b for b in doc.tree.walk() if doc.tree.kids(b) and doc.tree.depth(b) == 2)
    kids = set(doc.tree.kids(parent))
    doc.set_collapsed(parent, True)
    collapsed = build_scene(doc, measure, DiagramExportOptions())
    expanded = build_scene(doc, measure, DiagramExportOptions(expanded=True))
    assert not kids & labels(collapsed)
    assert kids <= labels(expanded)
    assert doc.tree.bones[parent].collapsed  # the project itself is unchanged


def test_export_scene_has_no_selection(doc, measure):
    scene = build_scene(doc, measure, DiagramExportOptions())
    assert scene.selected_bone() is None


def test_png_scale_dpi_and_size_cap(doc, measure, tmp_path):
    scene = build_scene(doc, measure, DiagramExportOptions())
    rect = scene.sceneRect()
    path = tmp_path / "d.png"
    export_png(scene, path, 2.0)
    img = QImage(str(path))
    assert (img.width(), img.height()) == png_size(scene, 2.0)
    assert abs(img.width() - rect.width() * 2) <= 1
    assert round(img.dotsPerMeterX() * 0.0254) == 192
    w, h = png_size(scene, 1000.0)
    assert max(w, h) <= MAX_PNG_SIDE


def test_png_text_is_drawn_at_layout_size(doc, measure):
    """Regression: setting the image DPI before painting enlarged (and clipped) the text.

    A 2× render, shrunk back to 1×, should look like the 1× render. With the bug the
    text was drawn at 4× inside 2× boxes, so the two differed about 3× more.
    """
    scene = build_scene(doc, measure, DiagramExportOptions(title_block=False, legend=False))
    one = render_image(scene, 1.0)
    two = render_image(scene, 2.0).scaled(one.width(), one.height(),
                                         Qt.AspectRatioMode.IgnoreAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
    total = count = 0
    for y in range(0, one.height(), 2):
        for x in range(0, one.width(), 2):
            total += abs(one.pixelColor(x, y).lightness() - two.pixelColor(x, y).lightness())
            count += 1
    assert total / count < 3.0  # ~1.6 when correct, ~5 with the bug


def test_svg_and_pdf_are_written_at_screen_dpi(doc, measure, tmp_path):
    scene = build_scene(doc, measure, DiagramExportOptions(title="Café ✓"))
    svg, pdf = tmp_path / "d.svg", tmp_path / "d.pdf"
    export_svg(scene, svg, title="Café ✓")
    export_pdf(scene, pdf, "A4", "landscape")
    text = svg.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<?xml") and "<svg" in text
    assert "Café ✓" in text  # title, UTF-8
    assert pdf.read_bytes().startswith(b"%PDF")
    # Point-sized fonts at 96 dpi: the SVG's font sizes match the screen.
    assert "font-size" in text


def test_export_file_picks_format_by_extension(doc, measure, tmp_path):
    scene = build_scene(doc, measure, DiagramExportOptions())
    for ext in ("png", "svg", "pdf"):
        export_file(scene, tmp_path / f"x.{ext}")
        assert (tmp_path / f"x.{ext}").stat().st_size > 1000
    with pytest.raises(ValueError):
        export_file(scene, tmp_path / "x.bmp")


def test_outline_text(doc):
    text = outline_text(doc)
    lines = text.splitlines()
    assert lines[0] == doc.project.name
    assert lines[1] == f"Problem: {doc.project.problem_statement}"
    first_cat = doc.tree.roots()[0]
    first_cause = doc.tree.kids(first_cat)[0]
    cat_title = doc.tree.bones[first_cat].title
    cat_line = next(line for line in lines if line.startswith(f"- 1 {cat_title}"))
    assert "complete" in cat_line
    cause_line = next(line for line in lines
                      if line.startswith(f"\t- 1.1 {doc.tree.bones[first_cause].title}"))
    assert "(auto)" in cause_line or "—" in cause_line
    assert sum(1 for line in lines if line.lstrip("\t").startswith("- ")) == len(doc.tree.bones)
    plain = outline_text(doc, details=False)
    assert "—" not in plain


def test_project_json_round_trips_structure(doc, tmp_path):
    path = tmp_path / "p.json"
    write_json(doc, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == "fishy-project" and data["format_version"] == 1
    assert data["project"]["name"] == doc.project.name
    assert [lv["name"] for lv in data["levels"]] == [lv.name for lv in doc.ordered_levels()]

    seen, images = [], []

    def walk(nodes, depth=1):
        for n in nodes:
            seen.append(n["id"])
            assert n["number"] == doc.numbers[n["id"]]
            assert (n["kind"] == "category") == (depth == 1)
            if n["kind"] == "cause":
                for e in n["log_entries"]:
                    images.extend(e["attachments"])
            else:
                assert "log_entries" not in n and "progress" in n
            walk(n["children"], depth + 1)

    walk(data["bones"])
    assert seen == list(doc.tree.walk())
    assert images, "the sample has an image"
    png = base64.b64decode(images[0]["data_base64"])
    assert png.startswith(b"\x89PNG")
    lean = project_dict(doc, include_images=False)
    assert lean["images_included"] is False


def test_safe_filename():
    assert safe_filename('Line 3: seal "failures"?') == "Line 3 seal failures"
    assert safe_filename("...") == "fishbone"


# =========================================================================== #
# Dialog and window
# =========================================================================== #


@pytest.fixture
def win(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w._set_doc(create_sample_project(tmp_path / "s.fishy"))
    return w


def test_dialog_exports_each_format_and_remembers_choices(win, qtbot, tmp_path):
    for fmt in ("png", "svg", "pdf"):
        dlg = ExportDialog(win.doc, win.measure, win.settings, str(win.doc.path), win)
        qtbot.addWidget(dlg)
        dlg.format_buttons[fmt].setChecked(True)
        dlg.page.setCurrentText("A3")
        dlg.title_edit.setText("Board review")
        assert dlg.export(str(tmp_path / f"out_{fmt}"))  # extension added
        assert (tmp_path / f"out_{fmt}.{fmt}").exists()
    assert win.settings.value("export/format") == "pdf"
    assert win.settings.value("export/page") == "A3"
    again = ExportDialog(win.doc, win.measure, win.settings, str(win.doc.path), win)
    qtbot.addWidget(again)
    assert again.format() == "pdf" and again.page.currentText() == "A3"
    assert again.title_edit.text() == win.doc.project.name  # title is per project
    assert again.suggested_path().endswith(".pdf")


def test_dialog_png_size_label_and_preview(win, qtbot):
    dlg = ExportDialog(win.doc, win.measure, win.settings, str(win.doc.path), win)
    qtbot.addWidget(dlg)
    dlg.format_buttons["png"].setChecked(True)
    dlg.scale.setCurrentIndex(dlg.scale.findData(4.0))
    dlg.refresh()
    w, h = png_size(dlg.scene, 4.0)
    assert f"{w:,}" in dlg.png_info.text()
    assert not dlg.preview.pixmap().isNull()
    dlg.format_buttons["pdf"].setChecked(True)
    dlg.refresh()
    assert not dlg.preview.pixmap().isNull()
    assert dlg.size_stack.currentIndex() == 2


def test_expanded_option_only_when_something_is_collapsed(win, qtbot):
    dlg = ExportDialog(win.doc, win.measure, win.settings, str(win.doc.path), win)
    qtbot.addWidget(dlg)
    assert not dlg.expanded.isEnabled()
    parent = next(b for b in win.doc.tree.walk() if win.doc.tree.kids(b))
    win.doc.set_collapsed(parent, True)
    dlg2 = ExportDialog(win.doc, win.measure, win.settings, str(win.doc.path), win)
    qtbot.addWidget(dlg2)
    assert dlg2.expanded.isEnabled()


def test_copy_image_and_outline_to_clipboard(win):
    win.copy_diagram_image()
    image = QGuiApplication.clipboard().image()
    assert not image.isNull() and image.width() > 1000
    win.copy_outline()
    assert QGuiApplication.clipboard().text().startswith(win.doc.project.name)


def test_export_actions_disabled_without_project(win):
    assert all(a.isEnabled() for a in win.export_actions)
    win._set_doc(None)
    assert not any(a.isEnabled() for a in win.export_actions)
