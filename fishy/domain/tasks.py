"""Pure rules for tasks: done/overdue, state toggling, roll-up and sorting.

No Qt, no SQL. `states` is always a mapping of task-state id to `TaskState`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from .model import BoneTree, StatusRole, Task, TaskState

States = Mapping[int, TaskState]


def _ordered(states: States) -> list[TaskState]:
    return sorted(states.values(), key=lambda s: s.sort_order)


def is_done(task: Task, states: States) -> bool:
    st = states.get(task.state_id)
    return st is not None and st.role == StatusRole.DONE


def is_overdue(task: Task, states: States, today: date) -> bool:
    if not task.due_date or is_done(task, states):
        return False
    try:
        return date.fromisoformat(task.due_date) < today
    except ValueError:
        return False


def default_state_id(states: States) -> int:
    ordered = _ordered(states)
    return next((s.id for s in ordered if s.is_default), ordered[0].id)


def done_state_id(states: States) -> int:
    return next(s.id for s in _ordered(states) if s.role == StatusRole.DONE)


def toggle_target(task: Task, states: States) -> int:
    """Open → first done state; done → the default state."""
    return default_state_id(states) if is_done(task, states) else done_state_id(states)


def next_state_id(task: Task, states: States) -> int:
    """The next state in sort order, wrapping around."""
    ids = [s.id for s in _ordered(states)]
    if task.state_id not in ids:
        return ids[0]
    return ids[(ids.index(task.state_id) + 1) % len(ids)]


def task_sort_key(task: Task, states: States) -> tuple:
    """Open first, then by due date (no date last), then by id."""
    return (is_done(task, states), task.due_date is None, task.due_date or "", task.id)


def task_groups(tree: BoneTree, links: Mapping[int, set[int]],
                bone_id: int) -> list[tuple[int, list[int]]]:
    """The tasks shown for a bone, grouped (spec §4.5).

    The first group is the bone's direct links. A category also gets one group per
    child cause, holding tasks linked anywhere in that cause's subtree that aren't
    already listed. Empty groups are dropped; ids within a group are ascending.
    """
    seen: set[int] = set()
    groups: list[tuple[int, list[int]]] = []

    def add(group_bone: int, ids: Iterable[int]) -> None:
        fresh = sorted(set(ids) - seen)
        seen.update(fresh)
        if fresh:
            groups.append((group_bone, fresh))

    add(bone_id, links.get(bone_id, set()))
    bone = tree.bones.get(bone_id)
    if bone is not None and bone.is_category:
        for cause in tree.kids(bone_id):
            ids: set[int] = set(links.get(cause, set()))
            for sub in tree.walk(cause):
                ids |= links.get(sub, set())
            add(cause, ids)
    return groups


def rolled_task_ids(tree: BoneTree, links: Mapping[int, set[int]], bone_id: int) -> list[int]:
    return [t for _, ids in task_groups(tree, links, bone_id) for t in ids]


def distinct_assignees(tasks: Iterable[Task]) -> list[str]:
    """Names used so far: trimmed, de-duplicated ignoring case (first spelling wins)."""
    by_key: dict[str, str] = {}
    for t in tasks:
        name = (t.assignee or "").strip()
        if name:
            by_key.setdefault(name.casefold(), name)
    return sorted(by_key.values(), key=str.casefold)


def number_sort_key(number: str) -> tuple[int, ...]:
    """Natural order for section numbers: 1.2 before 1.10; empty last."""
    if not number:
        return (10**9,)
    try:
        return tuple(int(p) for p in number.split("."))
    except ValueError:
        return (10**9,)
