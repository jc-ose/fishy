"""fishy main window: diagram + tree-grid + details, always in sync (UI-2, UI-3, UI-5).

Run with:  uv run main.py [path/to/project.fishy]
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from fishy import theme
from fishy.document import ProjectDocument
from fishy.domain import CATEGORY_TEMPLATES, StatusRole
from fishy.filtering import BoneFilter, FilterResult, apply_filter
from fishy.layout import PRESETS, LayoutNode, layout_fishbone
from fishy.project_lock import ProjectLock
from fishy.storage import PROJECT_SUFFIX, Repository
from fishy.views.bone_tree_view import BoneTreeView, swatch
from fishy.views.details_panel import DetailsPanel
from fishy.views.diagram_view import DiagramScene, DiagramView, qt_measure
from fishy.views.filter_panel import FilterPanel
from fishy.views.settings_dialog import (
    SettingsDialog,
    display_name,
    new_project_style,
    new_project_template,
)

FILE_FILTER = f"fishy project (*{PROJECT_SUFFIX})"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.doc: ProjectDocument | None = None
        self._lock: ProjectLock | None = None  # FR-7.7
        self.measure = qt_measure()
        self.settings = settings or QSettings("fishy", "fishy")
        self.resize(1440, 920)
        app = QApplication.instance()
        self.themes = theme.manager() or theme.install(app, self.settings)
        self.themes.changed.connect(self._theme_applied)

        # ----- panes ----------------------------------------------------- #
        self.scene = DiagramScene(self)
        self.view = DiagramView(self.scene)
        self.tree = BoneTreeView()
        self.details = DetailsPanel(settings=self.settings)

        self.editor_split = QSplitter(Qt.Orientation.Horizontal)
        self.editor_split.addWidget(self.tree)
        self.editor_split.addWidget(self.details)
        self.editor_split.setStretchFactor(0, 3)
        self.editor_split.setStretchFactor(1, 2)
        self.editor_split.setSizes([860, 420])

        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split.addWidget(self.view)
        self.main_split.addWidget(self.editor_split)
        self.main_split.setStretchFactor(0, 3)
        self.main_split.setStretchFactor(1, 2)
        self.main_split.setSizes([520, 380])
        self.main_split.setChildrenCollapsible(False)
        self.setCentralWidget(self.main_split)

        # ----- signals between panes ------------------------------------- #
        self.scene.boneSelected.connect(self._select_from_diagram)
        self.scene.boneActivated.connect(self._activate_from_diagram)
        self.scene.boneContextMenu.connect(self._bone_menu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.addSiblingRequested.connect(self.add_sibling)
        self.tree.addChildRequested.connect(self.add_child)
        self.tree.indentRequested.connect(self.indent)
        self.tree.outdentRequested.connect(self.outdent)
        self.tree.moveUpRequested.connect(lambda b: self._keep(b, self.doc.move_up))
        self.tree.moveDownRequested.connect(lambda b: self._keep(b, self.doc.move_down))
        self.tree.deleteRequested.connect(self.delete_bone)
        self.details.evidence.boneLinkClicked.connect(self.select_bone)
        self.timeline: QDialog | None = None
        self.tasks_window: QDialog | None = None

        self._build_toolbars()
        self._restore_layout()
        self._set_doc(None)

    # ===================================================================== #
    # UI construction
    # ===================================================================== #

    def _act(self, text: str, slot, shortcut: str | None = None, tip: str | None = None,
             checkable: bool = False) -> QAction:
        act = QAction(text, self)
        act.setCheckable(checkable)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if tip or shortcut:
            act.setToolTip(f"{tip or text}" + (f" ({shortcut})" if shortcut else ""))
        return act

    def _build_toolbars(self) -> None:
        bar = QToolBar("Main", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addAction(self._act("New…", self.new_project, "Ctrl+N"))
        bar.addAction(self._act("New sample…", self.new_sample,
                                tip="Create a project pre-filled with an example"))
        self.open_act = self._act("Open…", lambda: self.open_project(), "Ctrl+O")
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        open_button = QToolButton()
        open_button.setDefaultAction(self.open_act)
        open_button.setMenu(self.recent_menu)
        open_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        open_button.setToolTip("Open a project (Ctrl+O); the arrow lists recent projects")
        bar.addWidget(open_button)
        self.copy_act = self._act("Save copy…", self.save_copy,
                                  tip="Write a copy of this project to another file")
        bar.addAction(self.copy_act)
        # Export (FR-8): the button opens the diagram dialog; its menu has the rest.
        self.export_act = self._act("Export…", self.export_diagram, "Ctrl+Shift+E",
                                    tip="Export the diagram as PNG, SVG or PDF")
        self.copy_image_act = self._act("Copy diagram image", self.copy_diagram_image,
                                        "Ctrl+Shift+C",
                                        tip="Copy the diagram to the clipboard as an image")
        self.outline_act = self._act("Export outline (text)…", self.export_outline,
                                     tip="The bones as a tab-indented bulleted list")
        self.copy_outline_act = self._act("Copy outline", self.copy_outline,
                                          tip="Copy the bone outline to the clipboard as text")
        self.json_act = self._act("Export project (JSON)…", self.export_json,
                                  tip="Everything in the project, for interchange or backup")
        export_menu = QMenu(self)
        export_menu.addAction(self.export_act)
        export_menu.addAction(self.copy_image_act)
        export_menu.addSeparator()
        export_menu.addAction(self.outline_act)
        export_menu.addAction(self.copy_outline_act)
        export_menu.addAction(self.json_act)
        self.export_button = QToolButton()
        self.export_button.setDefaultAction(self.export_act)
        self.export_button.setMenu(export_menu)
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        bar.addWidget(self.export_button)
        self.tasks_csv_act = self._act("Export tasks (CSV)…", self.export_tasks_csv,
                                       tip="Every task, for a spreadsheet")
        export_menu.addAction(self.tasks_csv_act)
        self.export_actions = [self.export_act, self.copy_image_act, self.outline_act,
                               self.copy_outline_act, self.json_act, self.tasks_csv_act]
        bar.addSeparator()
        self.undo_act = self._act("Undo", lambda: self.doc and self.doc.undo_stack.undo(),
                                  "Ctrl+Z")
        self.redo_act = self._act("Redo", lambda: self.doc and self.doc.undo_stack.redo(),
                                  "Ctrl+Y")
        bar.addAction(self.undo_act)
        bar.addAction(self.redo_act)
        bar.addSeparator()
        self.edit_actions = [
            self._act("Add category", lambda: self.add_category(), tip="Add a category"),
            self._act("Add cause", lambda: self.add_child(self.tree.current_bone()),
                      tip="Add a cause under the selection (Tab in the tree)"),
            self._act("Delete", lambda: self.delete_bone(self.tree.current_bone()),
                      tip="Delete the selection (Delete in the tree)"),
        ]
        for act in self.edit_actions:
            bar.addAction(act)
        bar.addSeparator()
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find (Ctrl+F)")
        self.find_box.setClearButtonEnabled(True)
        self.find_box.setFixedWidth(200)
        self.find_box.textEdited.connect(lambda _t: self.find_next(restart=True))
        self.find_box.returnPressed.connect(self.find_next)
        bar.addWidget(self.find_box)
        find_act = QAction(self)
        find_act.setShortcut(QKeySequence.StandardKey.Find)
        find_act.triggered.connect(lambda: (self.find_box.setFocus(), self.find_box.selectAll()))
        self.addAction(find_act)

        # Filter (UI-7): a drop-down panel; ticking boxes doesn't close it.
        self.bone_filter = BoneFilter()
        self.filter_result = None
        self.filter_panel = FilterPanel()
        self.filter_panel.filterChanged.connect(self._filter_changed)
        filter_menu = QMenu(self)
        panel_act = QWidgetAction(filter_menu)
        panel_act.setDefaultWidget(self.filter_panel)
        filter_menu.addAction(panel_act)
        filter_menu.aboutToShow.connect(lambda: QTimer.singleShot(
            0, self.filter_panel.text.setFocus))
        self.filter_button = QToolButton()
        self.filter_button.setText("Filter")
        self.filter_button.setToolTip("Show only bones with a given level, status or text "
                                      "(Ctrl+Shift+F)")
        self.filter_button.setMenu(filter_menu)
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_button.setCheckable(True)
        bar.addWidget(self.filter_button)
        filter_act = QAction(self)
        filter_act.setShortcut(QKeySequence("Ctrl+Shift+F"))
        filter_act.triggered.connect(self.filter_button.showMenu)
        self.addAction(filter_act)
        clear_act = self._act("✕", lambda: self.filter_panel.clear(),
                              tip="Clear the filter and show every bone")
        self.clear_filter_act = clear_act
        clear_act.setVisible(False)
        bar.addAction(clear_act)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(self._act("Settings…", self.edit_settings, "Ctrl+,",
                                tip="Your name, theme, and defaults for new projects"))
        bar.addSeparator()
        # Pane layout (UI-2): show both, or maximize either pane.
        self.pane_group = QActionGroup(self)
        self.pane_actions = {}
        for key, label, shortcut in (("both", "Both", "Ctrl+3"),
                                     ("diagram", "Diagram", "Ctrl+1"),
                                     ("editor", "Editor", "Ctrl+2")):
            act = self._act(label, lambda _=False, k=key: self.show_panes(k), shortcut,
                            tip=f"Show {'both panes' if key == 'both' else 'only the ' + key}",
                            checkable=True)
            self.pane_group.addAction(act)
            self.pane_actions[key] = act
            bar.addAction(act)
        self.pane_actions["both"].setChecked(True)
        bar.addAction(self._act("⇄", self.toggle_orientation,
                                tip="Diagram above / beside the editor"))

        self.addToolBarBreak()
        bar2 = QToolBar("Project", self)
        bar2.setMovable(False)
        self.addToolBar(bar2)
        bar2.addWidget(QLabel(" Problem: "))
        self.problem_edit = QLineEdit()
        self.problem_edit.setPlaceholderText("The effect being investigated (shown in the head)")
        self.problem_edit.setMinimumWidth(340)
        self.problem_edit.editingFinished.connect(self._problem_edited)
        bar2.addWidget(self.problem_edit)
        bar2.addSeparator()
        bar2.addWidget(QLabel(" Style: "))
        self.style_box = QComboBox()
        self.style_box.addItems(list(PRESETS))
        self.style_box.currentTextChanged.connect(self._style_changed)
        bar2.addWidget(self.style_box)
        self.rollup_act = self._act("Level roll-up", self._rollup_toggled, checkable=True,
                                    tip="Parents take the most severe level of their causes")
        bar2.addAction(self.rollup_act)
        self.status_act = self._act("Status dots", self._status_toggled, checkable=True,
                                    tip="Show each bone's status on the diagram")
        bar2.addAction(self.status_act)
        self.legend_act = self._act("Legend", self._legend_toggled, checkable=True,
                                    tip="Show the legend on the diagram and in exports")
        bar2.addAction(self.legend_act)
        self.legend_pos = QComboBox()
        for key, label in (("bottom-left", "Bottom left"), ("bottom-right", "Bottom right"),
                           ("right", "Right side")):
            self.legend_pos.addItem(label, key)
        self.legend_pos.setToolTip("Legend position")
        self.legend_pos.activated.connect(self._legend_moved)
        bar2.addWidget(self.legend_pos)
        bar2.addSeparator()
        self.choices_act = self._act("Definitions", self.edit_choices,
                                     tip="Edit level and status names, task states, colors and order")
        bar2.addAction(self.choices_act)
        self.timeline_act = self._act("Timeline", self.show_timeline, "Ctrl+T",
                                      tip="Every evidence entry in the project, by date")
        bar2.addAction(self.timeline_act)
        self.tasks_act = self._act("Tasks", self.show_tasks, "Ctrl+Shift+T",
                                   tip="Every task in the project: sort, filter, edit, export")
        bar2.addAction(self.tasks_act)
        bar2.addSeparator()
        bar2.addAction(self._act("Fit", self.view.fit_all, "Ctrl+0", tip="Fit diagram"))
        bar2.addAction(self._act("Zoom to selection", self.view.zoom_to_selection, "Ctrl+E"))
        bar2.addSeparator()
        bar2.addWidget(QLabel(" Theme: "))
        self.theme_box = QComboBox()
        for key, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            self.theme_box.addItem(label, key)
        self.theme_box.setToolTip("Follow the Windows light/dark setting, or choose one. "
                                  "The diagram stays white, like the exported image.")
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(self.themes.mode)))
        self.theme_box.activated.connect(self._theme_chosen)
        bar2.addWidget(self.theme_box)

        self.progress_label = QLabel()
        self.user_label = QLabel()
        self.statusBar().addPermanentWidget(self.progress_label)
        self.statusBar().addPermanentWidget(self.user_label)

    # ===================================================================== #
    # Pane layout (UI-2), remembered between sessions
    # ===================================================================== #

    def show_panes(self, which: str) -> None:
        self.view.setVisible(which in ("both", "diagram"))
        self.editor_split.setVisible(which in ("both", "editor"))
        self.pane_actions[which].setChecked(True)
        if which in ("both", "diagram"):
            self.view.request_fit()

    def toggle_orientation(self) -> None:
        vertical = self.main_split.orientation() == Qt.Orientation.Vertical
        self.main_split.setOrientation(Qt.Orientation.Horizontal if vertical
                                       else Qt.Orientation.Vertical)
        self.editor_split.setOrientation(Qt.Orientation.Vertical if vertical
                                         else Qt.Orientation.Horizontal)
        self.view.request_fit()

    def _restore_layout(self) -> None:
        geom = self.settings.value("window/geometry")
        if isinstance(geom, QByteArray):
            self.restoreGeometry(geom)
        if self.settings.value("window/orientation") == "horizontal":
            self.toggle_orientation()
        for split, key in ((self.main_split, "main"), (self.editor_split, "editor")):
            state = self.settings.value(f"window/split_{key}")
            if isinstance(state, QByteArray):
                split.restoreState(state)
        self.show_panes(self.settings.value("window/panes", "both"))

    def _save_layout(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/orientation",
                               "horizontal" if self.main_split.orientation()
                               == Qt.Orientation.Horizontal else "vertical")
        self.settings.setValue("window/split_main", self.main_split.saveState())
        self.settings.setValue("window/split_editor", self.editor_split.saveState())
        which = next(k for k, a in self.pane_actions.items() if a.isChecked())
        self.settings.setValue("window/panes", which)

    # ===================================================================== #
    # Document lifecycle
    # ===================================================================== #

    def _set_doc(self, doc: ProjectDocument | None, lock: ProjectLock | None = None) -> None:
        if self.doc is not None:
            from fishy.views.tasks import close_task_editors

            close_task_editors(self.doc)  # they save typed text, so before the file closes
            self.details.set_document(None)
            self.doc.close()
            self.doc.deleteLater()
        if self._lock is not None:
            self._lock.release()
        self._lock = lock
        self.doc = doc
        enabled = doc is not None
        if self.timeline is not None:
            self.timeline.close()
            self.timeline = None
        if self.tasks_window is not None:
            self.tasks_window.close()
            self.tasks_window = None
        for act in [*self.edit_actions, *self.export_actions, self.copy_act, self.rollup_act,
                    self.status_act, self.legend_act, self.choices_act, self.timeline_act,
                    self.tasks_act]:
            act.setEnabled(enabled)
        for w in (self.problem_edit, self.style_box, self.find_box, self.tree, self.legend_pos,
                  self.filter_button):
            w.setEnabled(enabled)
        self.bone_filter = BoneFilter()
        self.filter_result = None
        self.filter_panel.set_document(doc)
        self._update_filter_widgets()
        self.undo_act.setEnabled(False)
        self.redo_act.setEnabled(False)
        if doc is None:
            self.setWindowTitle("fishy")
            self.tree.setModel(None)
            self.details.set_document(None)
            self.scene.clear()
            self.progress_label.setText("")
            self.statusBar().showMessage("Create or open a project to begin.")
            return

        doc.setParent(self)
        self.setWindowTitle(f"fishy — {doc.project.name} — {doc.path}")
        self.tree.attach(doc)
        doc.model.confirm_kind_change = self._confirm_kind_change
        self.tree.selectionModel().currentChanged.connect(self._tree_current_changed)
        doc.model.rowsInserted.connect(self._expand_parent)
        self.details.set_document(doc)

        # Bound methods (not lambdas): Qt drops these when either side is destroyed.
        doc.changed.connect(self.relayout)
        doc.changed.connect(self._update_progress)
        doc.changed.connect(self._reapply_filter)
        doc.choicesChanged.connect(self.filter_panel.rebuild)
        doc.projectChanged.connect(self._sync_project_widgets)
        doc.error.connect(self._show_error)
        doc.undo_stack.canUndoChanged.connect(self.undo_act.setEnabled)
        doc.undo_stack.canRedoChanged.connect(self.redo_act.setEnabled)
        doc.undo_stack.undoTextChanged.connect(self._undo_text)
        doc.undo_stack.redoTextChanged.connect(self._redo_text)

        self._show_user()
        self.settings.setValue("last_project", str(doc.path))
        self._remember_recent(doc.path)
        self._sync_project_widgets()
        self.relayout()
        self._update_progress()
        self.view.request_fit()
        self.tree.setFocus()
        self.statusBar().showMessage("Every change is saved to the file as you make it.", 6000)

    def _ask_path(self, title: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self, title, f"investigation{PROJECT_SUFFIX}",
                                              FILE_FILTER)
        if not path:
            return None
        p = Path(path)
        if p.suffix != PROJECT_SUFFIX:
            p = p.with_suffix(PROJECT_SUFFIX)
        if p.exists():  # the dialog already confirmed overwriting
            p.unlink()
        return p

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok or not name.strip():
            return
        templates = list(CATEGORY_TEMPLATES)
        template, ok = QInputDialog.getItem(
            self, "New project", "Category template:", templates,
            templates.index(new_project_template(self.settings)), False)
        if not ok:
            return
        path = self._ask_path("Create project")
        if path:
            from fishy.views.choices_dialog import saved_level_template

            levels = saved_level_template(self.settings)  # FR-3.6
            lock = self._claim(path)
            self._set_doc(ProjectDocument.create(
                path, name.strip(), "", template, display_name(self.settings), levels,
                style_preset=new_project_style(self.settings)), lock or None)

    def new_sample(self) -> None:
        path = self._ask_path("Create sample project")
        if path:
            lock = self._claim(path)
            self._set_doc(create_sample_project(path, display_name(self.settings)),
                          lock or None)

    def open_project(self, path: str | Path | None = None) -> bool:
        if not path:
            folder = str(Path(self.doc.path).parent) if self.doc else ""
            path, _ = QFileDialog.getOpenFileName(self, "Open project", folder, FILE_FILTER)
        if not path:
            return False
        if self.doc is not None and Path(path).resolve() == Path(self.doc.path).resolve():
            self.statusBar().showMessage("That project is already open here.", 4000)
            return True
        lock = self._claim(path)
        if lock is False:
            return False
        try:
            doc = ProjectDocument.open(path, display_name(self.settings))
        except Exception as exc:  # show any open failure to the user
            if lock:
                lock.release()
            if isinstance(exc, FileNotFoundError):
                self._forget_recent(path)
            QMessageBox.critical(self, "Can't open project", str(exc))
            return False
        self._set_doc(doc, lock or None)
        return True

    # ----- already open elsewhere (FR-7.7) -------------------------------- #

    def _claim(self, path: str | Path) -> ProjectLock | None | bool:
        """Lock `path`. Returns the lock, None to open without one, or False to cancel."""
        lock = ProjectLock(path)
        if lock.acquire():
            return lock
        holder = lock.holder()
        where = holder.describe() if holder else "somewhere else"
        return None if self._ask_open_anyway(Path(path).name, where) else False

    def _ask_open_anyway(self, name: str, where: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, "Project already open",
                          f"“{name}” is already open {where}.", parent=self)
        box.setInformativeText(
            "If it's open in two places at once, each shows out-of-date information and "
            "changes can overwrite each other. Close it there first if you can.\n\n"
            "If fishy crashed on another computer, it's safe to open anyway.")
        anyway = box.addButton("Open anyway", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is anyway

    # ----- recent projects (FR-7.6) --------------------------------------- #

    RECENT_KEY = "recent_projects"
    RECENT_MAX = 8

    def recent_projects(self) -> list[str]:
        value = self.settings.value(self.RECENT_KEY, [])
        if isinstance(value, str):  # QSettings returns a 1-item list as a string
            value = [value] if value else []
        return [str(v) for v in (value or [])]

    def _remember_recent(self, path) -> None:
        path = str(Path(path).resolve())
        items = [p for p in self.recent_projects()
                 if Path(p).resolve() != Path(path)]
        self.settings.setValue(self.RECENT_KEY, [path, *items][: self.RECENT_MAX])

    def _forget_recent(self, path) -> None:
        self.settings.setValue(self.RECENT_KEY, [
            p for p in self.recent_projects() if Path(p).resolve() != Path(path).resolve()])

    def _fill_recent_menu(self) -> None:
        menu = self.recent_menu
        menu.clear()
        items = self.recent_projects()
        if not items:
            menu.addAction("No recent projects").setEnabled(False)
            return
        for i, path in enumerate(items, start=1):
            p = Path(path)
            label = f"&{i}  {p.stem}" if i < 10 else p.stem
            act = menu.addAction(f"{label}    —  {p.parent}")
            act.setToolTip(path)
            if p.exists():
                act.triggered.connect(lambda _=False, x=path: self.open_project(x))
            else:
                act.setText(f"{label}  (missing)    —  {p.parent}")
                act.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Clear recent projects",
                       lambda: self.settings.setValue(self.RECENT_KEY, []))

    # ----- settings (UI-8) ------------------------------------------------- #

    def edit_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() and self.doc is not None:
            self.doc.repo.set_user(display_name(self.settings))
            self._show_user()

    def _show_user(self) -> None:
        user = self.doc.repo.users()[self.doc.repo.user_id].display_name
        self.user_label.setText(f"  User: {user} ")

    def save_copy(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy", "", FILE_FILTER)
        if path and self.doc:
            self.doc.repo.save_copy(path)
            self.statusBar().showMessage(f"Copy saved to {path}", 5000)

    # ===================================================================== #
    # Export (FR-8)
    # ===================================================================== #

    def export_diagram(self) -> None:
        from fishy.views.export_dialog import ExportDialog

        if not self.doc:
            return
        self.details._flush()
        dlg = ExportDialog(self.doc, self.measure, self.settings, str(self.doc.path), self)
        if dlg.exec() and dlg.last_path:
            self.statusBar().showMessage(f"Exported to {dlg.last_path}", 6000)
        elif dlg.copied:
            self.statusBar().showMessage("Diagram copied to the clipboard", 4000)

    def copy_diagram_image(self) -> None:
        """One-step copy with the dialog's last choices (FR-8.4)."""
        from fishy.export import build_scene, default_options, render_image

        if not self.doc:
            return
        self.details._flush()
        opts = default_options(self.doc)
        get = self.settings.value
        opts.title_block = str(get("export/title_block", True)).lower() == "true"
        opts.show_date = str(get("export/show_date", True)).lower() == "true"
        if str(get("export/show_author", True)).lower() != "true":
            opts.author = ""
        image = render_image(build_scene(self.doc, self.measure, opts), 2.0)
        QApplication.clipboard().setImage(image)
        self.statusBar().showMessage("Diagram copied to the clipboard", 4000)

    def _data_path(self, title: str, suffix: str, file_filter: str) -> str | None:
        from fishy.views.export_dialog import safe_filename

        folder = self.settings.value("export/folder", "") or str(Path(self.doc.path).parent)
        suggested = str(Path(folder) / f"{safe_filename(self.doc.project.name)}{suffix}")
        path, _ = QFileDialog.getSaveFileName(self, title, suggested, file_filter)
        if not path:
            return None
        if not path.lower().endswith(suffix):
            path += suffix
        self.settings.setValue("export/folder", str(Path(path).parent))
        return path

    def export_outline(self) -> None:
        from fishy.export import write_outline

        if self.doc and (path := self._data_path("Export outline", ".txt", "Text (*.txt)")):
            self.details._flush()
            write_outline(self.doc, path)
            self.statusBar().showMessage(f"Outline exported to {path}", 6000)

    def copy_outline(self) -> None:
        from fishy.export import outline_text

        if self.doc:
            self.details._flush()
            QApplication.clipboard().setText(outline_text(self.doc))
            self.statusBar().showMessage("Outline copied to the clipboard", 4000)

    def export_json(self) -> None:
        from fishy.export import write_json

        if self.doc and (path := self._data_path("Export project as JSON", ".json",
                                                 "JSON (*.json)")):
            self.details._flush()
            write_json(self.doc, path)
            self.statusBar().showMessage(f"Project exported to {path}", 6000)

    def closeEvent(self, event) -> None:
        self.details._flush()
        self._save_layout()
        self._set_doc(None)
        super().closeEvent(event)

    # ===================================================================== #
    # Diagram and project-level widgets
    # ===================================================================== #

    def relayout(self) -> None:
        if not self.doc:
            return
        p = self.doc.project
        style = replace(PRESETS.get(p.style_preset, PRESETS["Default"]),
                        show_status=p.show_status)
        diagram = layout_fishbone(p.problem_statement or p.name, self.doc.layout_nodes(),
                                  style, self.measure)
        self.scene.set_diagram(diagram, self.doc.legend_data(), p.legend_position)
        if self.view.auto_fit:
            self.view.fit_all()

    def _update_progress(self) -> None:
        """FR-3a.5: overall progress across all causes."""
        doc = self.doc
        causes = [b for b in doc.tree.bones.values() if not b.is_category]
        roles = [doc.statuses[b.status_id].role for b in causes if b.status_id in doc.statuses]
        done = sum(r is StatusRole.DONE for r in roles)
        blocked = sum(r is StatusRole.BLOCKED for r in roles)
        text = f"{done}/{len(causes)} causes complete"
        if blocked:
            text += f" · {blocked} blocked"
        self.progress_label.setText(text + "  ")

    def _sync_project_widgets(self) -> None:
        p = self.doc.project
        self.setWindowTitle(f"fishy — {p.name} — {self.doc.path}")
        if not self.problem_edit.hasFocus():
            self.problem_edit.setText(p.problem_statement)
        self.style_box.blockSignals(True)
        self.style_box.setCurrentText(p.style_preset)
        self.style_box.blockSignals(False)
        for act, value in ((self.rollup_act, p.level_rollup), (self.status_act, p.show_status),
                           (self.legend_act, p.show_legend)):
            act.blockSignals(True)
            act.setChecked(value)
            act.blockSignals(False)
        self.legend_pos.setCurrentIndex(self.legend_pos.findData(p.legend_position))
        self.legend_pos.setEnabled(p.show_legend)

    def _problem_edited(self) -> None:
        text = self.problem_edit.text().strip()
        if self.doc and text != self.doc.project.problem_statement:
            self.doc.set_project_fields(problem_statement=text)

    def _style_changed(self, name: str) -> None:
        if self.doc and name != self.doc.project.style_preset:
            self.doc.set_project_fields(style_preset=name)

    def _rollup_toggled(self, on: bool) -> None:
        if self.doc and on != self.doc.project.level_rollup:
            self.doc.set_project_fields(level_rollup=on)

    def _status_toggled(self, on: bool) -> None:
        if self.doc and on != self.doc.project.show_status:
            self.doc.set_project_fields(show_status=on)

    def _legend_toggled(self, on: bool) -> None:
        if self.doc and on != self.doc.project.show_legend:
            self.doc.set_project_fields(show_legend=on)

    def _legend_moved(self, _index: int) -> None:
        pos = self.legend_pos.currentData()
        if self.doc and pos != self.doc.project.legend_position:
            self.doc.set_project_fields(legend_position=pos)

    def edit_choices(self) -> None:
        from fishy.views.choices_dialog import ChoicesDialog

        if self.doc:
            ChoicesDialog(self.doc, self.settings, self).exec()

    def show_timeline(self) -> None:
        from fishy.views.evidence import TimelineDialog

        if not self.doc:
            return
        if self.timeline is None:
            self.timeline = TimelineDialog(self.doc, self)
            self.timeline.openEntry.connect(self.open_entry)
        self.timeline.show()
        self.timeline.raise_()
        self.timeline.activateWindow()

    def show_tasks(self) -> None:
        from fishy.views.tasks import TasksWindow

        if not self.doc:
            return
        if self.tasks_window is None:
            self.tasks_window = TasksWindow(self.doc, self)
            self.tasks_window.boneSelected.connect(self.select_bone)
            self.tasks_window.openEntry.connect(self.open_entry)
        self.tasks_window.show()
        self.tasks_window.raise_()
        self.tasks_window.activateWindow()

    def export_tasks_csv(self) -> None:
        from fishy.export import write_tasks_csv

        if self.doc and (path := self._data_path("Export tasks as CSV", ".csv",
                                                 "CSV (*.csv)")):
            self.details._flush()
            try:
                write_tasks_csv(self.doc, path)
            except OSError as exc:
                QMessageBox.warning(self, "Export tasks",
                                    f"Couldn't write {path}:\n{exc}\n\nIs it open in another "
                                    "program (e.g. Excel)?")
                return
            self.statusBar().showMessage(f"Exported {len(self.doc.tasks)} tasks to {path}",
                                         5000)

    def open_entry(self, bone_id: int, entry_id: int) -> None:
        if not self.editor_split.isVisible():
            self.show_panes("both")
        self.select_bone(bone_id)
        self.details.reveal_entry(entry_id)

    def _show_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"⚠ {msg}", 8000)

    def _undo_text(self, text: str) -> None:
        self.undo_act.setToolTip(f"Undo {text} (Ctrl+Z)" if text else "Nothing to undo")

    def _redo_text(self, text: str) -> None:
        self.redo_act.setToolTip(f"Redo {text} (Ctrl+Y)" if text else "Nothing to redo")

    # ===================================================================== #
    # Selection sync (UI-5)
    # ===================================================================== #

    def _expand_parent(self, parent: QModelIndex, *_args) -> None:
        self.tree.expand(parent)

    def _tree_current_changed(self, current: QModelIndex, _prev: QModelIndex) -> None:
        bone = self.tree.current_bone()
        self.details.show_bone(bone)
        if self.filter_result is not None:
            # A bone kept visible only because it was current drops out once you move on;
            # a faded bone picked on the diagram is shown. Deferred: not mid-selection.
            QTimer.singleShot(0, self, self._reapply_filter)  # dropped if the window closes
        item = self.scene.select_bone(bone)
        if item is not None and self.view.isVisible():
            self.view.ensureVisible(item, 40, 40)

    def select_bone(self, bone_id: int | None) -> None:
        if not self.doc or bone_id is None or bone_id not in self.doc.tree.bones:
            return
        if bone_id != self.tree.current_bone():
            idx = self.doc.model.index_for(bone_id)
            self.tree.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows)
            self.tree.scrollTo(idx)
        self.details.show_bone(bone_id)

    def _select_from_diagram(self, bone_id: int | None) -> None:
        if bone_id is not None:
            self.select_bone(bone_id)

    def _activate_from_diagram(self, bone_id: int) -> None:
        """Double-click on the diagram: open the bone for editing (FR-2.5)."""
        if not self.editor_split.isVisible():
            self.show_panes("both")
        self.select_bone(bone_id)
        self.details.focus_title()

    # ===================================================================== #
    # Editing
    # ===================================================================== #

    def _keep(self, bone_id: int, fn) -> None:
        """Run a move and keep the moved bone selected."""
        if self.doc and fn(bone_id):
            self.select_bone(bone_id)

    def _added(self, bone_id: int | None) -> None:
        if bone_id is not None:
            self.select_bone(bone_id)
            self.tree.edit_title(bone_id)

    def add_category(self) -> None:
        if self.doc:
            self._added(self.doc.add_bone(None, "New category"))

    def add_sibling(self, bone_id: int | None) -> None:
        if not self.doc:
            return
        if bone_id is None:
            self.add_category()
            return
        b = self.doc.bone(bone_id)
        title = "New category" if b.is_category else "New cause"
        self._added(self.doc.add_bone(b.parent_id, title, b.position + 1))

    def add_child(self, bone_id: int | None) -> None:
        if not self.doc:
            return
        if bone_id is None:
            self.add_category()
            return
        self.tree.expand(self.doc.model.index_for(bone_id))
        self._added(self.doc.add_bone(bone_id, "New cause"))

    def _confirm_kind_change(self, bone_id: int, new_kind: str) -> bool:
        title = self.doc.bone(bone_id).title
        if new_kind == "category":
            msg = (f"Make “{title}” a top-level category?\n\nCategories don't have their own "
                   "level, status or evidence log.")
        else:
            msg = (f"Make the category “{title}” a cause?\n\nIt will get the default level and "
                   "status.")
        return QMessageBox.question(self, "Change bone type", msg) == \
            QMessageBox.StandardButton.Yes

    def _move_with_confirm(self, bone_id: int, target, fn) -> None:
        if not self.doc or target is None:
            return
        kind = self.doc.kind_change(bone_id, target[0])
        if kind and not self._confirm_kind_change(bone_id, kind):
            return
        self._keep(bone_id, fn)

    def indent(self, bone_id: int) -> None:
        self._move_with_confirm(bone_id, self.doc.indent_target(bone_id), self.doc.indent)

    def outdent(self, bone_id: int) -> None:
        self._move_with_confirm(bone_id, self.doc.outdent_target(bone_id), self.doc.outdent)

    def delete_bone(self, bone_id: int | None) -> None:
        """FR-1.4: confirm when the bone has sub-causes or evidence; always undoable."""
        if not self.doc or bone_id is None:
            return
        b = self.doc.bone(bone_id)
        n = sum(1 for _ in self.doc.tree.walk(bone_id))
        logs = self.doc.log_counts.get(bone_id, 0) + sum(
            self.doc.log_counts.get(k, 0) for k in self.doc.tree.walk(bone_id))
        if n or logs:
            parts = [f"{n} sub-cause(s)"] if n else []
            if logs:
                parts.append(f"{logs} evidence entr{'y' if logs == 1 else 'ies'}")
            msg = f"Delete “{b.title}” with its {' and '.join(parts)}?\n\nYou can undo this."
            if QMessageBox.question(self, "Delete", msg) != QMessageBox.StandardButton.Yes:
                return
        # Select a neighbour so keyboard editing can continue.
        siblings = self.doc.tree.kids(b.parent_id)
        neighbour = (siblings[b.position + 1] if b.position + 1 < len(siblings)
                     else siblings[b.position - 1] if b.position > 0 else b.parent_id)
        if self.doc.delete_bone(bone_id):
            self.select_bone(neighbour)

    # ===================================================================== #
    # Context menus (tree rows and diagram bones)
    # ===================================================================== #

    def _tree_menu(self, pos: QPoint) -> None:
        idx = self.tree.indexAt(pos)
        if idx.isValid():
            self._bone_menu(self.tree.current_bone(), self.tree.viewport().mapToGlobal(pos))

    def _bone_menu(self, bone_id: int | None, global_pos: QPoint) -> None:
        if not self.doc or bone_id is None:
            return
        self.select_bone(bone_id)
        doc, bone = self.doc, self.doc.bone(bone_id)
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self.tree.edit_title(bone_id))
        if not bone.is_category:
            levels = menu.addMenu("Level")
            for lv in doc.ordered_levels():
                act = levels.addAction(swatch(lv.color), lv.name)
                act.setCheckable(True)
                act.setChecked(doc.effective.get(bone_id) == lv.id)
                act.triggered.connect(lambda _=False, i=lv.id: doc.set_level(bone_id, i))
            statuses = menu.addMenu("Status")
            for st in doc.ordered_statuses():
                act = statuses.addAction(swatch(st.color), st.name)
                act.setCheckable(True)
                act.setChecked(bone.status_id == st.id)
                act.triggered.connect(lambda _=False, i=st.id: doc.set_status(bone_id, i))
            if doc.tree.kids(bone_id) and doc.project.level_rollup:
                pin = menu.addAction("Pin level (override roll-up)")
                pin.setCheckable(True)
                pin.setChecked(bone.level_pinned)
                pin.triggered.connect(lambda on: doc.set_pinned(bone_id, on))
        menu.addSeparator()
        menu.addAction("Add cause", lambda: self.add_child(bone_id))
        menu.addAction("Add sibling", lambda: self.add_sibling(bone_id))
        if doc.tree.kids(bone_id):
            col = menu.addAction("Collapse in diagram")
            col.setCheckable(True)
            col.setChecked(bone.collapsed)
            col.triggered.connect(lambda on: doc.set_collapsed(bone_id, on))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_bone(bone_id))
        menu.exec(global_pos)

    # ===================================================================== #
    # Find (title text)
    # ===================================================================== #

    def find_next(self, restart: bool = False) -> None:
        text = self.find_box.text().strip().lower()
        if not self.doc or not text:
            return
        order = list(self.doc.tree.walk())
        current = self.tree.current_bone()
        start = 0
        if not restart and current in order:
            start = order.index(current) + 1
        for bid in order[start:] + order[:start]:
            if text in self.doc.bone(bid).title.lower():
                self.select_bone(bid)
                return
        self.statusBar().showMessage(f"No bone matches “{self.find_box.text()}”", 3000)

    # ===================================================================== #
    # Filter (UI-7)
    # ===================================================================== #

    def _filter_changed(self, f: BoneFilter) -> None:
        self.bone_filter = f
        self._reapply_filter()
        if self.filter_result is not None:
            shown = len(self.filter_result.matches)
            self.statusBar().showMessage(
                f"Filter: {self.bone_filter.describe(self.doc)} — {shown} of "
                f"{self.filter_result.total} bones match", 5000)

    def _reapply_filter(self) -> None:
        """Recompute after any edit: levels roll up, statuses change, bones move."""
        if self.doc is None:
            return
        result = apply_filter(self.doc, self.bone_filter)
        current = self.tree.current_bone()
        if result is not None and current is not None and current in self.doc.tree.bones \
                and current not in result.visible:
            # Keep the bone being worked on visible (e.g. one just added with Enter),
            # with its parents as context, until the selection moves on.
            context, parent = set(result.context), self.doc.tree.bones[current].parent_id
            while parent is not None:
                if parent not in result.matches:
                    context.add(parent)
                parent = self.doc.tree.bones[parent].parent_id
            result = FilterResult(result.matches | {current}, frozenset(context), result.total)
        self.filter_result = result
        self.scene.set_filter(result)
        self.tree.apply_filter(result)
        self._update_filter_widgets()

    def _update_filter_widgets(self) -> None:
        r = self.filter_result
        active = r is not None
        self.filter_button.setChecked(active)
        self.filter_button.setText(f"Filter: {len(r.matches)} of {r.total}" if active
                                   else "Filter")
        self.clear_filter_act.setVisible(active)

    # ===================================================================== #
    # Theme (UI-1)
    # ===================================================================== #

    def _theme_chosen(self, _index: int = 0) -> None:
        mode = self.theme_box.currentData()
        self.settings.setValue(theme.SETTINGS_KEY, mode)
        self.themes.set_mode(mode)

    def _theme_applied(self) -> None:
        # Stylesheet-driven widgets and Markdown views update themselves; the grid's
        # category tint and muted cells come from the model, so repaint those.
        if self.doc is not None:
            self.doc.model.refresh_derived()
        self.tree.viewport().update()
        index = self.theme_box.findData(self.themes.mode)
        if index != self.theme_box.currentIndex():
            self.theme_box.setCurrentIndex(index)


def create_sample_project(path: str | Path, display_name: str | None = None
                          ) -> ProjectDocument:
    """A project file pre-filled with the 26-bone sample investigation."""
    from fishy.samples import STATUSES, typical

    problem, cats = typical()
    repo = Repository.create(path, "Line 3 seal failures", problem, "Blank", display_name)
    levels = {lv.name: lv.id for lv in repo.levels()}
    statuses = {st.name: st.id for st in repo.statuses()}
    status_by_color = {color: statuses[name] for name, color in STATUSES.items()}

    def add(node: LayoutNode, parent: int | None) -> None:
        kwargs = {}
        if parent is not None:
            kwargs = dict(level_id=levels[node._level.name],  # type: ignore[attr-defined]
                          status_id=status_by_color.get(node.status_color))
        bone = repo.add_bone(parent, node.title, **kwargs)
        for child in node.children:
            add(child, bone.id)

    with repo.tx():
        for cat in cats:
            add(cat, None)
        _add_sample_evidence(repo)
    return ProjectDocument(repo)


def _sample_chart() -> bytes:
    """A small bar chart image for the sample evidence (drawn, not loaded)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    from fishy.views.evidence import png_bytes

    data = [("Day", 3), ("Swing", 5), ("Night", 17)]
    img = QImage(420, 220, QImage.Format.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Segoe UI")
    font.setPixelSize(13)
    p.setFont(font)
    p.setPen(QColor("#37474f"))
    p.drawText(QRectF(0, 6, 420, 20), Qt.AlignmentFlag.AlignCenter,
               "Seal failures by shift, week 41")
    for i, (label, value) in enumerate(data):
        x, h = 60 + i * 120, value * 9
        p.fillRect(QRectF(x, 190 - h, 70, h), QColor("#c62828" if value > 10 else "#90a4ae"))
        p.drawText(QRectF(x - 10, 192, 90, 20), Qt.AlignmentFlag.AlignCenter, label)
        p.drawText(QRectF(x - 10, 170 - h, 90, 18), Qt.AlignmentFlag.AlignCenter, str(value))
    p.end()
    return png_bytes(img)


def _add_sample_evidence(repo: Repository) -> None:
    def bone(prefix: str) -> int:
        return next(b.id for b in repo.bones() if b.title.startswith(prefix))

    training, sop = bone("Training record"), bone("SOP rev C")
    repo.add_log_entry(training, (
        "Training matrix for operator **#4417** shows the *SOP rev C* module as "
        "**not completed**.\n\n"
        "- [x] Pulled training matrix export\n"
        "- [x] Confirmed with shift supervisor\n"
        "- [ ] Check other night-shift operators\n\n"
        f"Related: [[bone:{sop}]]"), "Training records")
    thermo = bone("Thermocouple")
    readings = repo.add_log_entry(thermo, (
        "Calibration check against the reference probe:\n\n"
        "| Setpoint (°C) | Press reading | Reference | Error |\n"
        "|---:|---:|---:|---:|\n"
        "| 180 | 180.2 | 185.9 | **−5.7** |\n"
        "| 200 | 200.1 | 206.3 | **−6.2** |\n\n"
        "Drift is consistent at ~6 °C low. Query used to pull the readings:\n\n"
        "```sql\nSELECT ts, zone, temp_c FROM press_log\n"
        " WHERE line = 3 AND ts >= '2026-10-01'\n ORDER BY ts;\n```"), "Calibration check")
    repo.conn.execute("UPDATE log_entry SET created_at = '2026-10-14T13:05:00.000Z', "
                      "updated_at = '2026-10-14T13:05:00.000Z' WHERE id = ?", (readings.id,))
    hvac = bone("HVAC")
    entry = repo.add_log_entry(hvac, "", "Failures by shift")
    att = repo.add_attachment(entry.id, _sample_chart(), "image/png", "failures-by-shift.png")
    repo.update_log_entry(entry.id, body_md=(
        "Night shift accounts for most failures, when the HVAC setback is active:\n\n"
        f"![Seal failures by shift](attachment:{att})"))
    repo.conn.execute("UPDATE log_entry SET updated_at = created_at WHERE id = ?", (entry.id,))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("fishy")
    app.setStyle("Fusion")
    settings = QSettings("fishy", "fishy")
    theme.install(app, settings)  # before any window, so nothing flashes in the wrong colors
    win = MainWindow(settings)
    win.show()
    if len(argv) > 1:
        win.open_project(argv[1])
    else:
        last = win.settings.value("last_project")
        if last and Path(last).exists():
            win.open_project(last)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
