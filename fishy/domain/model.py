"""Domain types and pure rules: no Qt, no SQL.

The storage layer returns these dataclasses; the document/tree model and the
diagram consume them. Rules that are *derived* from stored data (numbering,
level roll-up, category status summaries) live here as pure functions, so they
are trivial to test and can never go stale in the database.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum


class StatusRole(StrEnum):
    """Fixed meaning behind a user-named status (used for summaries and future roll-up)."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"


@dataclass
class User:
    id: int
    display_name: str
    account_name: str | None = None


@dataclass
class Project:
    name: str
    problem_statement: str
    style_preset: str = "Default"
    level_rollup: bool = True
    show_legend: bool = True
    legend_position: str = "bottom-left"  # "bottom-left" | "bottom-right" | "right"
    show_status: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Level:
    id: int
    name: str
    color: str
    definition: str = ""
    sort_order: int = 0  # 0 = most severe; drives roll-up (FR-3.7)
    is_default: bool = False
    eliminated: bool = False  # drawn dimmed / struck through (e.g. Ruled Out)


@dataclass
class Status:
    id: int
    name: str
    color: str
    role: StatusRole = StatusRole.NOT_STARTED
    icon: str | None = None
    sort_order: int = 0
    is_default: bool = False


@dataclass
class TaskState:
    """A user-named task state; the role gives it a fixed meaning (done = complete)."""

    id: int
    name: str
    color: str
    symbol: str  # one glyph drawn as the checkbox
    role: StatusRole = StatusRole.NOT_STARTED
    sort_order: int = 0
    is_default: bool = False


@dataclass
class Task:
    id: int
    title: str
    state_id: int
    notes: str = ""
    assignee: str | None = None
    due_date: str | None = None  # ISO date 'YYYY-MM-DD'
    critical: bool = False
    bone_ids: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    created_by: int | None = None
    updated_by: int | None = None


@dataclass
class Bone:
    id: int
    parent_id: int | None
    position: int
    title: str
    description: str = ""
    level_id: int | None = None  # None for categories
    level_pinned: bool = False
    status_id: int | None = None  # None for categories
    collapsed: bool = False
    created_at: str = ""
    updated_at: str = ""
    created_by: int | None = None
    updated_by: int | None = None

    @property
    def is_category(self) -> bool:
        return self.parent_id is None


@dataclass
class LogEntry:
    id: int
    bone_id: int
    position: int
    body_md: str
    title: str | None = None
    created_at: str = ""
    updated_at: str = ""
    created_by: int | None = None
    updated_by: int | None = None


@dataclass
class Attachment:
    id: int
    log_entry_id: int | None
    mime: str
    sha256: str
    filename: str | None = None
    created_at: str = ""


# --------------------------------------------------------------------------- #
# Defaults (FR-3.2, FR-3a.2) and category templates (FR-1.5)
# --------------------------------------------------------------------------- #

DEFAULT_LEVELS: list[dict] = [
    dict(name="Root Cause", color="#c62828",
         definition="A fundamental cause which, if corrected, prevents recurrence."),
    dict(name="Direct Cause", color="#e65100",
         definition="A cause that directly resulted in the event."),
    dict(name="Immediate Cause", color="#b8860b",
         definition="The condition or action immediately preceding the event."),
    dict(name="Potential Contributor", color="#546e7a", is_default=True,
         definition="A plausible factor that has not yet been confirmed or ruled out."),
    dict(name="Ruled Out", color="#9e9e9e", eliminated=True,
         definition="Investigated and shown not to have contributed."),
]

DEFAULT_STATUSES: list[dict] = [
    dict(name="Not Started", color="#b0bec5", role=StatusRole.NOT_STARTED, is_default=True),
    dict(name="In Progress", color="#1e88e5", role=StatusRole.ACTIVE),
    dict(name="Blocked", color="#e53935", role=StatusRole.BLOCKED),
    dict(name="Under Review", color="#8e24aa", role=StatusRole.REVIEW),
    dict(name="Complete", color="#43a047", role=StatusRole.DONE),
]

CATEGORY_TEMPLATES: dict[str, list[str]] = {
    "6M": ["People", "Machine", "Method", "Material", "Measurement", "Environment"],
    "4S": ["Surroundings", "Suppliers", "Systems", "Skills"],
    "8P": ["Product", "Price", "Place", "Promotion", "People", "Process",
           "Physical Evidence", "Productivity"],
    "Blank": [],
}


# --------------------------------------------------------------------------- #
# Tree helpers and derived rules
# --------------------------------------------------------------------------- #


@dataclass
class BoneTree:
    """An in-memory index over a flat list of bones."""

    bones: dict[int, Bone] = field(default_factory=dict)
    children: dict[int | None, list[int]] = field(default_factory=dict)

    @classmethod
    def build(cls, bones: Iterable[Bone]) -> BoneTree:
        tree = cls()
        for b in bones:
            tree.bones[b.id] = b
        for b in sorted(tree.bones.values(), key=lambda b: b.position):
            tree.children.setdefault(b.parent_id, []).append(b.id)
        return tree

    def kids(self, bone_id: int | None) -> list[int]:
        return self.children.get(bone_id, [])

    def roots(self) -> list[int]:
        return self.kids(None)

    def walk(self, bone_id: int | None = None) -> Iterator[int]:
        """Depth-first, in display order, excluding `bone_id` itself."""
        for kid in self.kids(bone_id):
            yield kid
            yield from self.walk(kid)

    def depth(self, bone_id: int) -> int:
        d, b = 1, self.bones[bone_id]
        while b.parent_id is not None:
            d += 1
            b = self.bones[b.parent_id]
        return d


def numbers(tree: BoneTree) -> dict[int, str]:
    """Section-style numbers from position: 1, 1.2, 1.2.3 (FR-1.2)."""
    out: dict[int, str] = {}

    def walk(parent: int | None, prefix: str) -> None:
        for i, bid in enumerate(tree.kids(parent), start=1):
            out[bid] = f"{prefix}{i}"
            walk(bid, f"{out[bid]}.")

    walk(None, "")
    return out


def effective_levels(tree: BoneTree, levels: dict[int, Level],
                     rollup: bool) -> dict[int, int | None]:
    """The level each bone *displays* (FR-3.7).

    Leaves show their own level. With roll-up on, parents show the most severe
    level among their children (lowest sort_order) unless pinned; categories
    always roll up. With roll-up off, every cause shows its own level and
    categories still summarise their causes so they keep a meaningful color.
    """
    out: dict[int, int | None] = {}

    def severity(level_id: int | None) -> int:
        return levels[level_id].sort_order if level_id in levels else 10**6

    def walk(bid: int) -> int | None:
        bone = tree.bones[bid]
        child_levels = [walk(k) for k in tree.kids(bid)]
        own = bone.level_id if bone.level_id in levels else None
        if bone.is_category:
            candidates = [lv for lv in child_levels if lv is not None]
            result = min(candidates, key=severity) if candidates else None
        elif child_levels and rollup and not bone.level_pinned:
            candidates = [lv for lv in child_levels if lv is not None]
            result = min(candidates, key=severity) if candidates else own
        else:
            result = own
        out[bid] = result
        return result

    for root in tree.roots():
        walk(root)
    return out


def level_is_derived(tree: BoneTree, bone_id: int, rollup: bool) -> bool:
    """True when a bone's displayed level comes from roll-up, not its own field."""
    bone = tree.bones[bone_id]
    if bone.is_category:
        return True
    return rollup and not bone.level_pinned and bool(tree.kids(bone_id))


@dataclass(frozen=True)
class StatusSummary:
    total: int
    done: int
    blocked: int

    @property
    def text(self) -> str:
        if not self.total:
            return "No causes"
        extra = f", {self.blocked} blocked" if self.blocked else ""
        return f"{self.done}/{self.total} complete{extra}"


def category_summary(tree: BoneTree, category_id: int,
                     statuses: dict[int, Status]) -> StatusSummary:
    """Progress over every cause under a category (FR-3a.6)."""
    total = done = blocked = 0
    for bid in tree.walk(category_id):
        total += 1
        st = statuses.get(tree.bones[bid].status_id)  # type: ignore[arg-type]
        if st and st.role is StatusRole.DONE:
            done += 1
        elif st and st.role is StatusRole.BLOCKED:
            blocked += 1
    return StatusSummary(total, done, blocked)
