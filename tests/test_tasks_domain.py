"""Tasks: pure domain rules (no Qt, no SQL)."""

from __future__ import annotations

from datetime import date

from fishy.domain import Bone, BoneTree, StatusRole, Task, TaskState
from fishy.domain.tasks import (
    distinct_assignees,
    is_done,
    is_overdue,
    next_state_id,
    number_sort_key,
    rolled_task_ids,
    task_groups,
    task_sort_key,
    toggle_target,
)

S = {1: TaskState(1, "To do", "#90a4ae", "☐", StatusRole.NOT_STARTED, 0, True),
     2: TaskState(2, "In progress", "#1e88e5", "◐", StatusRole.ACTIVE, 1),
     3: TaskState(3, "Done", "#43a047", "☑", StatusRole.DONE, 2),
     4: TaskState(4, "Verified", "#2e7d32", "✔", StatusRole.DONE, 3)}


def test_is_done_by_role():
    assert is_done(Task(1, "t", 4), S) and not is_done(Task(1, "t", 2), S)


def test_toggle_open_goes_to_first_done_and_back_to_default():
    assert toggle_target(Task(1, "t", 2), S) == 3
    assert toggle_target(Task(1, "t", 4), S) == 1


def test_cycle_wraps():
    assert next_state_id(Task(1, "t", 3), S) == 4
    assert next_state_id(Task(1, "t", 4), S) == 1


def test_due_today_not_overdue():
    today = date(2026, 11, 19)
    assert not is_overdue(Task(1, "t", 1, due_date="2026-11-19"), S, today)
    assert is_overdue(Task(1, "t", 1, due_date="2026-11-18"), S, today)
    assert not is_overdue(Task(1, "t", 3, due_date="2026-11-01"), S, today)  # done
    assert not is_overdue(Task(1, "t", 1), S, today)  # no date


def test_sort_open_first_then_due_then_id():
    ts = [Task(5, "a", 3, due_date="2026-01-01"), Task(4, "b", 1),
          Task(3, "c", 1, due_date="2026-12-01"), Task(2, "d", 1, due_date="2026-11-01")]
    assert [t.id for t in sorted(ts, key=lambda t: task_sort_key(t, S))] == [2, 3, 4, 5]


def test_category_groups_dedupe_and_order():
    # category 1 -> causes 2, 3; cause 2 -> 4.  Task 10 on cat, 11 on 4 and 3, 12 on 3.
    tree = BoneTree.build([Bone(1, None, 0, "Cat"),
                           Bone(2, 1, 0, "A", level_id=1, status_id=1),
                           Bone(3, 1, 1, "B", level_id=1, status_id=1),
                           Bone(4, 2, 0, "A1", level_id=1, status_id=1)])
    links = {1: {10}, 4: {11}, 3: {11, 12}}
    assert task_groups(tree, links, 1) == [(1, [10]), (2, [11]), (3, [12])]
    assert rolled_task_ids(tree, links, 1) == [10, 11, 12]
    assert task_groups(tree, links, 2) == []  # parent causes don't roll up
    assert task_groups(tree, links, 3) == [(3, [11, 12])]


def test_distinct_assignees():
    ts = [Task(1, "a", 1, assignee=" J. Smith "), Task(2, "b", 1, assignee="j. smith"),
          Task(3, "c", 1, assignee="a. lee"), Task(4, "d", 1)]
    assert distinct_assignees(ts) == ["a. lee", "J. Smith"]


def test_number_sort_key():
    assert sorted(["1.10", "1.2", "", "2"], key=number_sort_key) == ["1.2", "1.10", "2", ""]
