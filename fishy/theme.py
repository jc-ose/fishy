"""Light and dark themes (UI-1).

One place owns every UI color. `ThemeManager` applies a theme to the whole app:

* an explicit Fusion `QPalette` for light or dark, so standard widgets look the
  same on every machine instead of half-following the OS;
* an app-wide stylesheet for fishy's own widgets. Widgets opt in with
  dynamic properties instead of hardcoding colors:
  ``muted`` (secondary text), ``small``, ``separator``, and ``#entryCard``
  frames with an optional ``highlight``;
* `current()` for code that draws or renders HTML itself (Markdown entries,
  the grid's category tint).

Mode is "system" (follow Windows; switches live when the OS setting changes),
"light" or "dark". The diagram canvas deliberately stays white in both modes,
like paper, so it always matches exports and the level colors keep their meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

MODES = ("system", "light", "dark")
SETTINGS_KEY = "appearance/theme"


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    window: str
    base: str  # text fields, lists
    alt_base: str
    text: str
    muted: str  # secondary text: hints, timestamps
    placeholder: str
    border: str
    card: str  # evidence cards
    accent: str  # selection, highlighted card
    link: str
    button: str
    disabled_text: str
    tooltip_base: str
    category_tint: tuple[int, int, int, int]  # grid category rows (RGBA)


LIGHT = Theme(
    name="light", dark=False,
    window="#f3f4f6", base="#ffffff", alt_base="#f6f8fa", text="#1f2328",
    muted="#5f6b74", placeholder="#8a939b", border="#d8dee4", card="#ffffff",
    accent="#2f74d0", link="#0969da", button="#eef0f3", disabled_text="#9aa3ab",
    tooltip_base="#ffffff", category_tint=(55, 71, 79, 24),
)

DARK = Theme(
    name="dark", dark=True,
    window="#1f2125", base="#26292d", alt_base="#2c2f34", text="#e6e8eb",
    muted="#9aa5ae", placeholder="#7d868e", border="#3d434a", card="#2a2d32",
    accent="#4a8fe7", link="#6cb6ff", button="#2f3338", disabled_text="#6c737a",
    tooltip_base="#2c2f34", category_tint=(255, 255, 255, 20),
)

_current: Theme = LIGHT


def current() -> Theme:
    return _current


def palette_for(t: Theme) -> QPalette:
    p = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    roles = {
        R.Window: t.window, R.WindowText: t.text, R.Base: t.base, R.AlternateBase: t.alt_base,
        R.Text: t.text, R.Button: t.button, R.ButtonText: t.text, R.BrightText: "#ffffff",
        R.Highlight: t.accent, R.HighlightedText: "#ffffff", R.Link: t.link,
        R.LinkVisited: t.link, R.ToolTipBase: t.tooltip_base, R.ToolTipText: t.text,
        R.PlaceholderText: t.placeholder, R.Mid: t.border, R.Midlight: t.alt_base,
        R.Light: t.alt_base if t.dark else "#ffffff", R.Dark: t.border,
        R.Shadow: "#000000",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, QColor(t.disabled_text))
    p.setColor(G.Inactive, R.Highlight, QColor(t.accent).darker(115 if t.dark else 105))
    return p


def stylesheet_for(t: Theme) -> str:
    return f"""
QLabel[muted="true"] {{ color: {t.muted}; }}
QLabel[small="true"] {{ font-size: 11px; }}
QFrame[separator="true"] {{ color: {t.border}; }}
QFrame#entryCard {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 6px; }}
QFrame#entryCard[highlight="true"] {{ border: 1px solid {t.accent}; }}
QToolTip {{ color: {t.text}; background: {t.tooltip_base}; border: 1px solid {t.border}; }}
"""


_glyph_cache: dict[tuple[str, str, int], QIcon] = {}


def glyph_icon(symbol: str, color: str, px: int = 20) -> QIcon:
    """A task-state symbol drawn as a bold, colored icon (sharp on HiDPI screens)."""
    key = (symbol, color, px)
    icon = _glyph_cache.get(key)
    if icon is None:
        scale = 2  # draw at 2x and let Qt scale down for crisp edges
        pix = QPixmap(px * scale, px * scale)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont()
        font.setPixelSize(round(px * scale * 0.95))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
        painter.end()
        pix.setDevicePixelRatio(scale)
        icon = _glyph_cache[key] = QIcon(pix)
    return icon


def mark(widget: QWidget, **props: bool) -> QWidget:
    """Tag a widget for the theme stylesheet, e.g. ``mark(label, muted=True)``."""
    for name, value in props.items():
        widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    return widget


def resolve(mode: str, app: QApplication | None = None) -> Theme:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    app = app or QApplication.instance()
    scheme = app.styleHints().colorScheme() if app else Qt.ColorScheme.Unknown
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


class ThemeManager(QObject):
    """Applies the chosen theme and re-applies it when Windows switches light/dark."""

    changed = Signal()

    def __init__(self, app: QApplication, settings: QSettings | None = None) -> None:
        super().__init__(app)
        self.app = app
        self.settings = settings
        saved = settings.value(SETTINGS_KEY, "system") if settings else "system"
        self.mode = saved if saved in MODES else "system"
        app.styleHints().colorSchemeChanged.connect(self._os_scheme_changed)
        self.apply()

    @property
    def theme(self) -> Theme:
        return _current

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode
        if self.settings is not None:
            self.settings.setValue(SETTINGS_KEY, mode)
        self.apply()

    def apply(self) -> None:
        global _current
        _current = resolve(self.mode, self.app)
        if self.app.style().name().lower() != "fusion":
            self.app.setStyle("Fusion")
        self.app.setPalette(palette_for(_current))
        self.app.setStyleSheet(stylesheet_for(_current))
        self.changed.emit()

    def _os_scheme_changed(self, *_args) -> None:
        if self.mode == "system":
            self.apply()


_manager: ThemeManager | None = None


def install(app: QApplication, settings: QSettings | None = None) -> ThemeManager:
    """Create (or replace) the app's theme manager and apply the saved mode."""
    global _manager
    if _manager is not None:
        try:
            app.styleHints().colorSchemeChanged.disconnect(_manager._os_scheme_changed)
        except (RuntimeError, TypeError):
            pass
    _manager = ThemeManager(app, settings)
    return _manager


def manager() -> ThemeManager | None:
    return _manager
