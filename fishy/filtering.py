"""Filtering bones by text, level and status (UI-7).

A filter never changes the project. It decides which bones *match*; the views
then show matches normally, keep their ancestors visible as context (muted), and
hide or dim everything else. Exports ignore the filter.

Rules:
* Each dimension is optional. Within a dimension any choice may match (OR);
  across dimensions all must match (AND).
* Text matches a bone's title, number or description, case-insensitively.
* Level means the level shown on the diagram, so rolled-up levels count.
* Categories have no status, so a status filter never matches a category
  directly; it shows up as context when any of its causes match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fishy.document import ProjectDocument


@dataclass(frozen=True)
class BoneFilter:
    text: str = ""
    level_ids: frozenset[int] = field(default_factory=frozenset)
    status_ids: frozenset[int] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        return bool(self.text.strip() or self.level_ids or self.status_ids)

    def describe(self, doc: ProjectDocument) -> str:
        parts = []
        if self.text.strip():
            parts.append(f"“{self.text.strip()}”")
        if self.level_ids:
            parts.append(" or ".join(doc.levels[i].name for i in sorted(self.level_ids)
                                     if i in doc.levels))
        if self.status_ids:
            parts.append(" or ".join(doc.statuses[i].name for i in sorted(self.status_ids)
                                     if i in doc.statuses))
        return ", ".join(parts)


@dataclass(frozen=True)
class FilterResult:
    matches: frozenset[int]  # shown normally
    context: frozenset[int]  # ancestors of matches: shown muted
    total: int

    @property
    def visible(self) -> frozenset[int]:
        return self.matches | self.context

    def state(self, bone_id: int | None) -> str:
        """'match', 'context' or 'out' (the head / unknown ids count as matches)."""
        if bone_id is None or bone_id in self.matches:
            return "match"
        return "context" if bone_id in self.context else "out"


def bone_matches(doc: ProjectDocument, bone_id: int, f: BoneFilter) -> bool:
    b = doc.tree.bones[bone_id]
    text = f.text.strip().lower()
    if text and not (text in b.title.lower() or text in (b.description or "").lower()
                     or doc.numbers.get(bone_id, "") == text):
        return False
    if f.level_ids:
        level = doc.effective_level(bone_id)
        if level is None or level.id not in f.level_ids:
            return False
    if f.status_ids and (b.is_category or b.status_id not in f.status_ids):
        return False
    return True


def apply_filter(doc: ProjectDocument, f: BoneFilter) -> FilterResult | None:
    """None when the filter is empty (everything shown)."""
    if not f.active:
        return None
    matches = {bid for bid in doc.tree.walk() if bone_matches(doc, bid, f)}
    context: set[int] = set()
    for bid in matches:
        parent = doc.tree.bones[bid].parent_id
        while parent is not None and parent not in context:
            context.add(parent)
            parent = doc.tree.bones[parent].parent_id
    context -= matches
    return FilterResult(frozenset(matches), frozenset(context), len(doc.tree.bones))
