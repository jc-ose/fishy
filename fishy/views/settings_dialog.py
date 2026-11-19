"""Settings (UI-8): your name, theme, and defaults for new projects.

All of these are app settings (this computer), not project settings. Project
settings — style, roll-up, legend, levels and statuses — stay with the project
file and are edited from the toolbar and the Levels & statuses dialog.
"""

from __future__ import annotations

import getpass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from fishy import theme
from fishy.domain import CATEGORY_TEMPLATES
from fishy.layout import PRESETS
from fishy.views.choices_dialog import LEVEL_TEMPLATE_KEY, saved_level_template

USER_NAME_KEY = "user/display_name"
NEW_STYLE_KEY = "new_project/style_preset"
NEW_TEMPLATE_KEY = "new_project/category_template"


def os_account_name() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user in the environment (rare on Windows)
        return "Unknown"


def display_name(settings: QSettings) -> str:
    """The name stamped on your changes (FR-6.5): from Settings, else the Windows account."""
    name = str(settings.value(USER_NAME_KEY, "") or "").strip()
    return name or os_account_name()


def new_project_style(settings: QSettings) -> str:
    style = str(settings.value(NEW_STYLE_KEY, "Default"))
    return style if style in PRESETS else "Default"


def new_project_template(settings: QSettings) -> str:
    template = str(settings.value(NEW_TEMPLATE_KEY, "6M"))
    return template if template in CATEGORY_TEMPLATES else "6M"


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        self.setMinimumWidth(460)

        you = QGroupBox("You")
        you_form = QFormLayout(you)
        self.name = QLineEdit(display_name(settings))
        self.name.setPlaceholderText(os_account_name())
        you_form.addRow("Your name", self.name)
        you_form.addRow("", theme.mark(QLabel(
            "Stamped on the bones and evidence you create or edit, and shown in exports.\n"
            "Leave empty to use your Windows account name."), muted=True, small=True))

        look = QGroupBox("Appearance")
        look_form = QFormLayout(look)
        self.theme = QComboBox()
        for key, label in (("system", "System (follow Windows)"), ("light", "Light"),
                           ("dark", "Dark")):
            self.theme.addItem(label, key)
        mgr = theme.manager()
        mode = mgr.mode if mgr else str(settings.value(theme.SETTINGS_KEY, "system"))
        self.theme.setCurrentIndex(max(0, self.theme.findData(mode)))
        look_form.addRow("Theme", self.theme)

        new = QGroupBox("New projects")
        new_form = QFormLayout(new)
        self.template = QComboBox()
        self.template.addItems(list(CATEGORY_TEMPLATES))
        self.template.setCurrentText(new_project_template(settings))
        new_form.addRow("Categories", self.template)
        self.style = QComboBox()
        self.style.addItems(list(PRESETS))
        self.style.setCurrentText(new_project_style(settings))
        new_form.addRow("Diagram style", self.style)
        self.levels_label = theme.mark(QLabel(), muted=True)
        self.levels_label.setWordWrap(True)
        self.reset_levels = QPushButton("Use built-in levels")
        self.reset_levels.clicked.connect(self._reset_levels)
        self._reset_pending = False
        levels_box = QVBoxLayout()
        levels_box.addWidget(self.levels_label)
        button_row = QHBoxLayout()
        button_row.addWidget(self.reset_levels)
        button_row.addStretch(1)
        levels_box.addLayout(button_row)
        new_form.addRow("Levels", levels_box)
        self._show_levels()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for box in (you, look, new):
            layout.addWidget(box)
        layout.addWidget(buttons)

    def _show_levels(self) -> None:
        levels = None if self._reset_pending else saved_level_template(self.settings)
        if levels:
            names = ", ".join(lv["name"] for lv in levels)
            self.levels_label.setText(f"Your saved set: {names}.")
            self.reset_levels.setEnabled(True)
        else:
            self.levels_label.setText("The built-in set. To start new projects with your own, "
                                      "choose “Use these levels for new projects” in a "
                                      "project's Levels & statuses dialog.")
            self.reset_levels.setEnabled(False)

    def _reset_levels(self) -> None:
        self._reset_pending = True
        self._show_levels()

    def name_value(self) -> str:
        return self.name.text().strip()

    def accept(self) -> None:
        s = self.settings
        s.setValue(USER_NAME_KEY, self.name_value())
        s.setValue(NEW_STYLE_KEY, self.style.currentText())
        s.setValue(NEW_TEMPLATE_KEY, self.template.currentText())
        if self._reset_pending:
            s.remove(LEVEL_TEMPLATE_KEY)
        mgr = theme.manager()
        if mgr is not None and mgr.mode != self.theme.currentData():
            s.setValue(theme.SETTINGS_KEY, self.theme.currentData())
            mgr.set_mode(self.theme.currentData())
        super().accept()
