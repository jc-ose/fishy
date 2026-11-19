"""Light/dark theming (UI-1): readable text everywhere, live switching."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from fishy import theme
from fishy.app import MainWindow, create_sample_project
from fishy.markdown_render import DARK_COLORS, LIGHT_COLORS, render


def luminance(color: str) -> float:
    c = QColor(color)

    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_text_is_readable_on_every_surface(t):
    for surface in (t.window, t.base, t.alt_base, t.card, t.button):
        assert contrast(t.text, surface) >= 7, surface
        assert contrast(t.muted, surface) >= 4.5, surface
    assert contrast("#ffffff", t.accent) >= 3  # selected rows


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_markdown_colors_readable_against_text(dark):
    t = theme.DARK if dark else theme.LIGHT
    c = DARK_COLORS if dark else LIGHT_COLORS
    assert contrast(t.text, c.code_bg) >= 7
    assert contrast(t.text, c.inline_code_bg) >= 4.5
    assert contrast(t.text, c.header_bg) >= 7
    assert contrast(c.link, t.card) >= 4.5
    assert contrast(c.quote, t.card) >= 4.5


def test_dark_markdown_uses_dark_code_blocks():
    md = "```python\nx = 1  # note\n```\n\n| a |\n|---|\n| b |"
    light, dark = render(md), render(md, dark=True)
    assert LIGHT_COLORS.code_bg in light and DARK_COLORS.code_bg not in light
    assert DARK_COLORS.code_bg in dark and LIGHT_COLORS.code_bg not in dark
    assert DARK_COLORS.table_border in dark


@pytest.fixture
def themed(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    mgr = theme.install(qapp, settings)
    yield mgr, settings
    mgr.set_mode("light")


def test_modes_apply_palette_and_persist(themed):
    mgr, settings = themed
    mgr.set_mode("dark")
    assert theme.current() is theme.DARK
    assert QApplication.palette().color(QPalette.ColorRole.Base) == QColor(theme.DARK.base)
    assert settings.value(theme.SETTINGS_KEY) == "dark"
    mgr.set_mode("light")
    assert QApplication.palette().color(QPalette.ColorRole.Text) == QColor(theme.LIGHT.text)
    with pytest.raises(ValueError):
        mgr.set_mode("sepia")


def test_saved_mode_is_restored(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue(theme.SETTINGS_KEY, "dark")
    mgr = theme.install(qapp, settings)
    try:
        assert mgr.mode == "dark" and theme.current().dark
    finally:
        mgr.set_mode("light")


def test_window_switches_theme_live(themed, qtbot, tmp_path):
    mgr, settings = themed
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win._set_doc(create_sample_project(tmp_path / "s.fishy"))
    win.show()
    # Pick a cause with evidence so cards and a Markdown body are on screen.
    bid = next(b for b in win.doc.tree.walk() if win.doc.log_counts.get(b))
    win.select_bone(bid)

    win.theme_box.setCurrentIndex(win.theme_box.findData("dark"))
    win._theme_chosen()
    assert theme.current().dark
    assert settings.value(theme.SETTINGS_KEY) == "dark"
    from fishy.views.markdown_view import MarkdownView

    bodies = win.details.findChildren(MarkdownView)
    assert bodies
    css = bodies[0].document().defaultStyleSheet()
    assert DARK_COLORS.inline_code_bg in css
    # The diagram stays white paper in both modes.
    assert win.scene.backgroundBrush().color() == QColor("white")
