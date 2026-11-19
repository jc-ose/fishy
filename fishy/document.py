"""`ProjectDocument`: the single mutation path for an open project.

Every edit goes through a document method, which:

1. performs the change in the repository (one SQL transaction, written to disk
   immediately: FR-7.1). If a domain rule rejects it, nothing changes and
   `error` is emitted;
2. pushes an undo command onto `undo_stack` (FR-7.5); undo/redo re-run the
   same repository calls, so every undo step is written through too;
3. updates the in-memory caches and the `BoneTreeModel` with precise
   insert/move/remove notifications;
4. emits `changed`, which the diagram listens to.

Data flow is one-directional (UI → document → repository → caches/model →
views), which keeps the diagram and the editor in sync (UI-5).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from fishy.domain import (
    Bone,
    BoneTree,
    Level,
    LogEntry,
    Project,
    Status,
    StatusRole,
    category_summary,
    effective_levels,
    level_is_derived,
    numbers,
)
from fishy.layout import LayoutNode
from fishy.storage import Repository
from fishy.storage.repository import RuleError

NEUTRAL_CATEGORY_COLOR = "#37474f"


class _Command(QUndoCommand):
    """Undo command whose first `redo` was already performed by the document."""

    def __init__(self, text: str, redo: Callable[[], Any], undo: Callable[[], Any]) -> None:
        super().__init__(text)
        self._redo, self._undo = redo, undo
        self._skip_first = True

    def redo(self) -> None:
        if self._skip_first:
            self._skip_first = False
            return
        self._redo()

    def undo(self) -> None:
        self._undo()


class ProjectDocument(QObject):
    changed = Signal()  # anything that affects the diagram or editor
    projectChanged = Signal()
    choicesChanged = Signal()  # levels or statuses
    logChanged = Signal(int)  # bone id whose evidence log changed
    error = Signal(str)

    def __init__(self, repo: Repository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from fishy.models.bone_tree_model import BoneTreeModel  # avoid import cycle

        self.repo = repo
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(500)
        self._reload_all()
        self.model = BoneTreeModel(self)

    # ----- opening ------------------------------------------------------- #

    @classmethod
    def create(cls, path: str | Path, name: str, problem: str = "", template: str = "6M",
               display_name: str | None = None,
               levels: list[dict] | None = None,
               style_preset: str = "Default") -> ProjectDocument:
        return cls(Repository.create(path, name, problem, template, display_name, levels,
                                     style_preset=style_preset))

    @classmethod
    def open(cls, path: str | Path, display_name: str | None = None) -> ProjectDocument:
        return cls(Repository.open(path, display_name))

    def close(self) -> None:
        self.repo.close()

    @property
    def path(self) -> Path | None:
        return self.repo.path

    # ----- caches -------------------------------------------------------- #

    def _reload_all(self) -> None:
        self._reload_project()
        self._reload_choices()
        self._reload_bones()

    def _reload_project(self) -> None:
        self.project: Project = self.repo.project()

    def _reload_choices(self) -> None:
        self.levels: dict[int, Level] = {lv.id: lv for lv in self.repo.levels()}
        self.statuses: dict[int, Status] = {st.id: st for st in self.repo.statuses()}
        self._derive()

    def _reload_bones(self) -> None:
        self.tree = BoneTree.build(self.repo.bones())
        self.log_counts = self.repo.log_entry_counts()
        self._derive()

    def _derive(self) -> None:
        if not hasattr(self, "tree"):
            return
        self.numbers = numbers(self.tree)
        self.effective = effective_levels(self.tree, self.levels, self.project.level_rollup)

    # ----- read helpers -------------------------------------------------- #

    def bone(self, bone_id: int) -> Bone:
        return self.tree.bones[bone_id]

    def ordered_levels(self) -> list[Level]:
        return sorted(self.levels.values(), key=lambda lv: lv.sort_order)

    def ordered_statuses(self) -> list[Status]:
        return sorted(self.statuses.values(), key=lambda st: st.sort_order)

    def effective_level(self, bone_id: int) -> Level | None:
        lid = self.effective.get(bone_id)
        return self.levels.get(lid) if lid is not None else None

    def level_is_derived(self, bone_id: int) -> bool:
        return level_is_derived(self.tree, bone_id, self.project.level_rollup)

    def summary(self, category_id: int):
        return category_summary(self.tree, category_id, self.statuses)

    def log_entries(self, bone_id: int) -> list[LogEntry]:
        return self.repo.log_entries(bone_id)

    # ----- the command machinery ----------------------------------------- #

    def _run(self, text: str, do: Callable[[], Any], undo: Callable[[], Any]) -> bool:
        """Perform `do` now; on success push an undoable command."""
        try:
            do()
        except (RuleError, KeyError, ValueError) as exc:
            self.error.emit(str(exc) if not isinstance(exc, KeyError) else "Item not found.")
            return False
        self.undo_stack.push(_Command(text, do, undo))
        return True

    # ===================================================================== #
    # Project fields
    # ===================================================================== #

    def set_project_fields(self, **fields: Any) -> bool:
        state: dict[str, Any] = {}

        def do() -> None:
            state["old"] = self.repo.update_project(**fields)
            self._after_project()

        def undo() -> None:
            self.repo.update_project(**state["old"])
            self._after_project()

        labels = {"level_rollup": "Change level roll-up", "show_legend": "Toggle legend",
                  "legend_position": "Move legend", "show_status": "Toggle status dots",
                  "style_preset": "Change style", "problem_statement": "Edit problem statement"}
        label = next((labels[k] for k in fields if k in labels), "Edit project")
        return self._run(label, do, undo)

    def _after_project(self) -> None:
        self._reload_project()
        self._derive()
        self.model.refresh_derived()
        self.projectChanged.emit()
        self.changed.emit()

    # ===================================================================== #
    # Bones
    # ===================================================================== #

    def add_bone(self, parent_id: int | None, title: str = "New cause",
                 index: int | None = None, **fields: Any) -> int | None:
        state: dict[str, Any] = {}

        def do() -> None:
            if "snap" in state:  # redo after undo: restore the same id
                self._restore(state["snap"])
                return
            bone = self.repo.add_bone(parent_id, title, index, **fields)
            state["id"] = bone.id
            self._model_insert(bone.parent_id, bone.position)

        def undo() -> None:
            state["snap"] = self._remove(state["id"])

        kind = "category" if parent_id is None else "cause"
        return state["id"] if self._run(f"Add {kind}", do, undo) else None

    def delete_bone(self, bone_id: int) -> bool:
        state: dict[str, Any] = {}

        def do() -> None:
            state["snap"] = self._remove(bone_id)

        def undo() -> None:
            self._restore(state["snap"])

        return self._run(f"Delete “{self.bone(bone_id).title}”", do, undo)

    def update_bone(self, bone_id: int, text: str | None = None, **fields: Any) -> bool:
        state: dict[str, Any] = {}

        def do() -> None:
            state["old"] = self.repo.update_bone(bone_id, **fields)
            self._after_bone_update(bone_id)

        def undo() -> None:
            self.repo.update_bone(bone_id, **state["old"])
            self._after_bone_update(bone_id)

        return self._run(text or "Edit bone", do, undo)

    def rename_bone(self, bone_id: int, title: str) -> bool:
        title = title.strip()
        if not title or title == self.bone(bone_id).title:
            return False
        return self.update_bone(bone_id, "Rename", title=title)

    def set_description(self, bone_id: int, description: str) -> bool:
        return self.update_bone(bone_id, "Edit description", description=description)

    def set_level(self, bone_id: int, level_id: int) -> bool:
        """Set a cause's own level. For a rolled-up parent this also pins it (FR-3.7)."""
        fields: dict[str, Any] = {"level_id": level_id}
        if self.level_is_derived(bone_id) and not self.bone(bone_id).is_category:
            fields["level_pinned"] = True
        return self.update_bone(bone_id, f"Set level to {self.levels[level_id].name}", **fields)

    def set_pinned(self, bone_id: int, pinned: bool) -> bool:
        return self.update_bone(bone_id, "Pin level" if pinned else "Unpin level",
                                level_pinned=pinned)

    def set_status(self, bone_id: int, status_id: int) -> bool:
        return self.update_bone(bone_id, f"Set status to {self.statuses[status_id].name}",
                                status_id=status_id)

    def set_collapsed(self, bone_id: int, collapsed: bool) -> None:
        """Diagram view state: saved with the project but not part of undo history."""
        try:
            self.repo.update_bone(bone_id, collapsed=collapsed)
        except (RuleError, KeyError) as exc:
            self.error.emit(str(exc))
            return
        self._reload_bones()
        self.changed.emit()

    def move_bone(self, bone_id: int, new_parent: int | None, new_index: int,
                  text: str = "Move") -> bool:
        state: dict[str, Any] = {}

        def do() -> None:
            old = self.bone(bone_id)
            state["before"] = self.repo.move_bone(bone_id, new_parent, new_index)
            self._model_move(bone_id, old.parent_id, old.position)

        def undo() -> None:
            cur = self.bone(bone_id)
            self.repo.restore_placement(bone_id, state["before"])
            self._model_move(bone_id, cur.parent_id, cur.position)

        return self._run(text, do, undo)

    # Keyboard-style moves (UI-6) expressed as `move_bone`.

    def indent_target(self, bone_id: int) -> tuple[int | None, int] | None:
        """Where indent would move the bone: last child of its previous sibling."""
        b = self.bone(bone_id)
        if b.position == 0:
            return None
        prev = self.tree.kids(b.parent_id)[b.position - 1]
        return prev, len(self.tree.kids(prev))

    def outdent_target(self, bone_id: int) -> tuple[int | None, int] | None:
        """Where outdent would move the bone: next sibling of its parent."""
        b = self.bone(bone_id)
        if b.parent_id is None:
            return None
        parent = self.bone(b.parent_id)
        return parent.parent_id, parent.position + 1

    def kind_change(self, bone_id: int, new_parent: int | None) -> str | None:
        """'category' / 'cause' if moving under `new_parent` converts the bone (FR-1a.2)."""
        b = self.bone(bone_id)
        if new_parent is None and not b.is_category:
            return "category"
        if new_parent is not None and b.is_category:
            return "cause"
        return None

    def indent(self, bone_id: int) -> bool:
        target = self.indent_target(bone_id)
        return bool(target) and self.move_bone(bone_id, *target, text="Indent")

    def outdent(self, bone_id: int) -> bool:
        target = self.outdent_target(bone_id)
        return bool(target) and self.move_bone(bone_id, *target, text="Outdent")

    def move_up(self, bone_id: int) -> bool:
        b = self.bone(bone_id)
        return b.position > 0 and self.move_bone(bone_id, b.parent_id, b.position - 1, "Move up")

    def move_down(self, bone_id: int) -> bool:
        b = self.bone(bone_id)
        last = len(self.tree.kids(b.parent_id)) - 1
        return b.position < last and self.move_bone(bone_id, b.parent_id, b.position + 1,
                                                    "Move down")

    # ----- bone helpers shared by do/undo -------------------------------- #

    def _remove(self, bone_id: int):
        b = self.bone(bone_id)
        snap = self.repo.delete_bone(bone_id)
        self.model.begin_remove(b.parent_id, b.position)
        self._reload_bones()
        self.model.end_remove()
        self._after_structure()
        return snap

    def _restore(self, snap) -> None:
        self.repo.restore_subtree(snap)
        root = snap["bones"][0]
        self._model_insert(root["parent_id"], root["position"])

    def _model_insert(self, parent_id: int | None, row: int) -> None:
        self.model.begin_insert(parent_id, row)
        self._reload_bones()
        self.model.end_insert()
        self._after_structure()

    def _model_move(self, bone_id: int, old_parent: int | None, old_row: int) -> None:
        new = self.repo.bone(bone_id)
        moved = self.model.begin_move(old_parent, old_row, new.parent_id, new.position)
        self._reload_bones()
        if moved:
            self.model.end_move()
        self._after_structure()

    def _after_bone_update(self, bone_id: int) -> None:
        self._reload_bones()
        self.model.refresh_derived()
        self.changed.emit()

    def _after_structure(self) -> None:
        self.model.refresh_derived()
        self.changed.emit()

    # ===================================================================== #
    # Levels and statuses
    # ===================================================================== #

    def _choice_command(self, text: str, do_fn: Callable[[], Any],
                        undo_fn: Callable[[Any], Any]) -> bool:
        state: dict[str, Any] = {}

        def do() -> None:
            state["r"] = do_fn()
            self._after_choices()

        def undo() -> None:
            undo_fn(state["r"])
            self._after_choices()

        return self._run(text, do, undo)

    def _after_choices(self) -> None:
        self._reload_choices()
        self._reload_bones()
        self.model.refresh_derived()
        self.choicesChanged.emit()
        self.changed.emit()

    def add_level(self, name: str, color: str, definition: str = "",
                  eliminated: bool = False, index: int | None = None) -> bool:
        state: dict[str, Any] = {}

        def do() -> int:
            if "snap" in state:
                self.repo.restore_choice(state["snap"])
                return state["id"]
            state["id"] = self.repo.add_level(name, color, definition, eliminated, index)
            return state["id"]

        def undo(level_id: int) -> None:
            state["snap"] = self.repo.delete_level(level_id, self._fallback_level(level_id))

        return self._choice_command(f"Add level “{name}”", do, undo)

    def update_level(self, level_id: int, **fields: Any) -> bool:
        return self._choice_command(
            "Edit level", lambda: self.repo.update_level(level_id, **fields),
            lambda old: self.repo.update_level(level_id, **old))

    def move_level(self, level_id: int, new_index: int) -> bool:
        return self._choice_command(
            "Reorder levels", lambda: self.repo.move_level(level_id, new_index),
            lambda old: self.repo.move_level(level_id, old))

    def set_default_level(self, level_id: int) -> bool:
        return self._choice_command(
            "Set default level", lambda: self.repo.set_default_level(level_id),
            lambda old: self.repo.set_default_level(old))

    def delete_level(self, level_id: int, reassign_to: int) -> bool:
        return self._choice_command(
            f"Delete level “{self.levels[level_id].name}”",
            lambda: self.repo.delete_level(level_id, reassign_to), self.repo.restore_choice)

    def add_status(self, name: str, color: str, role: StatusRole = StatusRole.ACTIVE,
                   index: int | None = None) -> bool:
        state: dict[str, Any] = {}

        def do() -> int:
            if "snap" in state:
                self.repo.restore_choice(state["snap"])
                return state["id"]
            state["id"] = self.repo.add_status(name, color, role, index=index)
            return state["id"]

        def undo(status_id: int) -> None:
            other = next(s for s in self.statuses if s != status_id)
            state["snap"] = self.repo.delete_status(status_id, other)

        return self._choice_command(f"Add status “{name}”", do, undo)

    def update_status(self, status_id: int, **fields: Any) -> bool:
        return self._choice_command(
            "Edit status", lambda: self.repo.update_status(status_id, **fields),
            lambda old: self.repo.update_status(status_id, **old))

    def move_status(self, status_id: int, new_index: int) -> bool:
        return self._choice_command(
            "Reorder statuses", lambda: self.repo.move_status(status_id, new_index),
            lambda old: self.repo.move_status(status_id, old))

    def set_default_status(self, status_id: int) -> bool:
        return self._choice_command(
            "Set default status", lambda: self.repo.set_default_status(status_id),
            lambda old: self.repo.set_default_status(old))

    def delete_status(self, status_id: int, reassign_to: int) -> bool:
        return self._choice_command(
            f"Delete status “{self.statuses[status_id].name}”",
            lambda: self.repo.delete_status(status_id, reassign_to), self.repo.restore_choice)

    def _fallback_level(self, level_id: int) -> int:
        return next(lv for lv in self.levels if lv != level_id)

    # ===================================================================== #
    # Evidence log (UI arrives in milestone 5)
    # ===================================================================== #

    def add_log_entry(self, bone_id: int, body_md: str = "", title: str | None = None,
                      index: int | None = None,
                      attachments: list[int] | None = None) -> int | None:
        """Add an entry; `attachments` are images added while it was being written."""
        state: dict[str, Any] = {}

        def do() -> None:
            if "snap" in state:
                self.repo.restore_log_entry(state["snap"])
            else:
                with self.repo.tx():
                    state["id"] = self.repo.add_log_entry(bone_id, body_md, title, index).id
                    self.repo.assign_attachments(attachments or [], state["id"])
            self._after_log(bone_id)

        def undo() -> None:
            state["snap"] = self.repo.delete_log_entry(state["id"])
            self._after_log(bone_id)

        return state["id"] if self._run("Add log entry", do, undo) else None

    def update_log_entry(self, entry_id: int, attachments: list[int] | None = None,
                         **fields: Any) -> bool:
        """Edit an entry. `attachments` (added while editing) are adopted by the entry and
        kept even if the edit is later undone, so redo never loses an image."""
        bone_id = self.repo.log_entry(entry_id).bone_id
        state: dict[str, Any] = {}

        def do() -> None:
            with self.repo.tx():
                state["old"] = self.repo.update_log_entry(entry_id, **fields)
                self.repo.assign_attachments(attachments or [], entry_id)
            self._after_log(bone_id)

        def undo() -> None:
            self.repo.update_log_entry(entry_id, **state["old"])
            self._after_log(bone_id)

        return self._run("Edit log entry", do, undo)

    def move_log_entry(self, entry_id: int, new_index: int) -> bool:
        bone_id = self.repo.log_entry(entry_id).bone_id
        state: dict[str, Any] = {}

        def do() -> None:
            state["old"] = self.repo.move_log_entry(entry_id, new_index)
            self._after_log(bone_id)

        def undo() -> None:
            self.repo.move_log_entry(entry_id, state["old"])
            self._after_log(bone_id)

        return self._run("Reorder log entries", do, undo)

    def delete_log_entry(self, entry_id: int) -> bool:
        bone_id = self.repo.log_entry(entry_id).bone_id
        state: dict[str, Any] = {}

        def do() -> None:
            state["snap"] = self.repo.delete_log_entry(entry_id)
            self._after_log(bone_id)

        def undo() -> None:
            self.repo.restore_log_entry(state["snap"])
            self._after_log(bone_id)

        return self._run("Delete log entry", do, undo)

    def _after_log(self, bone_id: int) -> None:
        self.log_counts = self.repo.log_entry_counts()
        self.model.refresh_derived()
        self.logChanged.emit(bone_id)

    # ===================================================================== #
    # Diagram
    # ===================================================================== #

    def legend_data(self):
        """Legend content for the current project settings, or None if hidden (FR-3.5)."""
        from fishy.views.legend import LegendData, LegendEntry

        if not self.project.show_legend:
            return None
        levels = [LegendEntry(lv.color, lv.name, lv.definition, lv.eliminated)
                  for lv in self.ordered_levels()]
        statuses = ([LegendEntry(st.color, st.name) for st in self.ordered_statuses()]
                    if self.project.show_status else [])
        return LegendData(levels, statuses)

    def layout_nodes(self, expanded: bool = False) -> list[LayoutNode]:
        """The bone tree as layout input: numbers, roll-up colors, status dots.

        `expanded=True` ignores collapsed state (FR-8.3 "export fully expanded").
        """

        def build(bid: int) -> LayoutNode:
            b = self.tree.bones[bid]
            level = self.effective_level(bid)
            status = self.statuses.get(b.status_id) if b.status_id else None
            color = level.color if level else NEUTRAL_CATEGORY_COLOR
            progress = None
            if b.is_category:
                summary = self.summary(bid)
                progress = (summary.done, summary.total)
            return LayoutNode(
                id=bid, title=b.title, number=self.numbers[bid],
                children=[build(k) for k in self.tree.kids(bid)],
                color=color, status_color=status.color if status else None,
                collapsed=b.collapsed and not expanded, dimmed=bool(level and level.eliminated),
                pinned=b.level_pinned and not b.is_category and bool(self.tree.kids(bid))
                and self.project.level_rollup,
                progress=progress,
            )

        return [build(r) for r in self.tree.roots()]
