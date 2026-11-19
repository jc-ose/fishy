"""Data exports (FR-8.5): an outline as text, and the whole project as JSON.

The outline is for pasting into email, reports or slides: one bullet per bone,
tabbed by depth. The JSON is for interchange and backup: everything in the
project file (bones, levels, statuses, evidence, images, users), plus the
computed numbers and rolled-up levels so other tools needn't recompute them.
"""

from __future__ import annotations

import base64
import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fishy import __version__ as APP_VERSION

if TYPE_CHECKING:
    from fishy.document import ProjectDocument

CSV_COLUMNS = ["ID", "State", "Done", "Title", "Bones", "Assignee", "Due", "Critical",
               "Overdue", "Notes", "Created", "Created by", "Updated", "Updated by"]

JSON_FORMAT = "fishy-project"
JSON_VERSION = 1


# =========================================================================== #
# Outline
# =========================================================================== #


def outline_text(doc: ProjectDocument, details: bool = True, bullet: str = "-") -> str:
    """The bone tree as a tab-indented bulleted list.

    With `details`, each line ends with its level and status, e.g.
    ``-\t1.1 Operator unfamiliar with new SOP — Root Cause (auto), In Progress``.
    """
    p = doc.project
    lines = [p.name]
    if p.problem_statement:
        lines.append(f"Problem: {p.problem_statement}")
    lines.append("")
    for bid in doc.tree.walk():
        b = doc.tree.bones[bid]
        indent = "\t" * (doc.tree.depth(bid) - 1)
        line = f"{indent}{bullet} {doc.numbers[bid]} {b.title}"
        if details:
            line += _details(doc, bid)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _details(doc: ProjectDocument, bid: int) -> str:
    b = doc.tree.bones[bid]
    level = doc.effective_level(bid)
    parts = []
    if level:
        auto = doc.level_is_derived(bid)
        parts.append(level.name + (" (auto)" if auto else " (pinned)" if b.level_pinned
                                   and doc.tree.kids(bid) else ""))
    if b.is_category:
        s = doc.summary(bid)
        if s.total:
            parts.append(f"{s.done}/{s.total} complete" + (f", {s.blocked} blocked"
                                                           if s.blocked else ""))
    else:
        st = doc.statuses.get(b.status_id)  # type: ignore[arg-type]
        if st:
            parts.append(st.name)
    return f" — {', '.join(parts)}" if parts else ""


# =========================================================================== #
# JSON
# =========================================================================== #


def project_dict(doc: ProjectDocument, include_images: bool = True) -> dict[str, Any]:
    repo = doc.repo
    p = doc.project
    users = repo.users()
    entries_by_bone: dict[int, list] = {}
    for e in repo.all_log_entries():
        entries_by_bone.setdefault(e.bone_id, []).append(e)

    def entry_dict(e) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": e.id, "title": e.title, "body_md": e.body_md, "position": e.position,
            "created_at": e.created_at, "updated_at": e.updated_at,
            "created_by": e.created_by, "updated_by": e.updated_by,
            "attachments": [],
        }
        for att in repo.attachments(e.id):
            a: dict[str, Any] = {"id": att.id, "filename": att.filename, "mime": att.mime}
            if include_images:
                a["data_base64"] = base64.b64encode(repo.attachment_data(att.id)).decode("ascii")
            out["attachments"].append(a)
        return out

    def bone_dict(bid: int) -> dict[str, Any]:
        b = doc.tree.bones[bid]
        level = doc.effective_level(bid)
        out: dict[str, Any] = {
            "id": b.id, "number": doc.numbers[bid], "kind": "category" if b.is_category
            else "cause", "title": b.title, "description": b.description,
            "level_id": b.level_id, "level_pinned": b.level_pinned,
            "effective_level_id": level.id if level else None,
            "level_derived": doc.level_is_derived(bid),
            "status_id": b.status_id, "collapsed": b.collapsed,
            "created_at": b.created_at, "updated_at": b.updated_at,
            "created_by": b.created_by, "updated_by": b.updated_by,
        }
        if b.is_category:
            s = doc.summary(bid)
            out["progress"] = {"total": s.total, "done": s.done, "blocked": s.blocked}
        else:
            out["log_entries"] = [entry_dict(e) for e in
                                  sorted(entries_by_bone.get(bid, []), key=lambda e: e.position)]
        out["children"] = [bone_dict(k) for k in doc.tree.kids(bid)]
        return out

    return {
        "format": JSON_FORMAT,
        "format_version": JSON_VERSION,
        "app_version": APP_VERSION,
        "images_included": include_images,
        "project": {
            "name": p.name, "problem_statement": p.problem_statement,
            "style_preset": p.style_preset, "level_rollup": p.level_rollup,
            "show_legend": p.show_legend, "legend_position": p.legend_position,
            "show_status": p.show_status,
            "created_at": p.created_at, "updated_at": p.updated_at,
        },
        "levels": [{"id": lv.id, "name": lv.name, "color": lv.color,
                    "definition": lv.definition, "severity_order": lv.sort_order,
                    "is_default": lv.is_default, "eliminated": lv.eliminated}
                   for lv in doc.ordered_levels()],
        "statuses": [{"id": st.id, "name": st.name, "color": st.color, "role": str(st.role),
                      "icon": st.icon, "order": st.sort_order, "is_default": st.is_default}
                     for st in doc.ordered_statuses()],
        "users": [{"id": u.id, "display_name": u.display_name, "account_name": u.account_name}
                  for u in users.values()],
        "bones": [bone_dict(r) for r in doc.tree.roots()],
    }


def write_json(doc: ProjectDocument, path: str | Path, include_images: bool = True) -> None:
    text = json.dumps(project_dict(doc, include_images), indent=2, ensure_ascii=False)
    Path(path).write_text(text + "\n", encoding="utf-8")


def write_outline(doc: ProjectDocument, path: str | Path, details: bool = True) -> None:
    Path(path).write_text(outline_text(doc, details), encoding="utf-8")


# =========================================================================== #
# Tasks (CSV)
# =========================================================================== #


def task_rows(doc: ProjectDocument, task_ids: list[int] | None = None) -> list[list[str]]:
    """One row per task, columns as `CSV_COLUMNS`; all tasks by id when `task_ids` is None."""
    from fishy.models.bone_tree_model import local_time

    users = doc.repo.users()

    def who(uid: int | None) -> str:
        return users[uid].display_name if uid in users else ""

    def yes(flag: bool) -> str:
        return "yes" if flag else "no"

    ids = sorted(doc.tasks) if task_ids is None else [t for t in task_ids if t in doc.tasks]
    rows = []
    for tid in ids:
        task = doc.tasks[tid]
        st = doc.task_states.get(task.state_id)
        bones = "; ".join(f"{doc.numbers.get(b, '')} {doc.bone(b).title}".strip()
                          for b in task.bone_ids if b in doc.tree.bones)
        rows.append([str(tid), st.name if st else "", yes(doc.is_task_done(tid)), task.title,
                     bones, task.assignee or "", task.due_date or "", yes(task.critical),
                     yes(doc.is_task_overdue(tid)), task.notes, local_time(task.created_at),
                     who(task.created_by), local_time(task.updated_at), who(task.updated_by)])
    return rows


def write_tasks_csv(doc: ProjectDocument, path: str | Path,
                    task_ids: list[int] | None = None) -> None:
    """Tasks as CSV: UTF-8 with BOM and CRLF, so Excel reads glyphs and accents."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n")
        writer.writerow(CSV_COLUMNS)
        writer.writerows(task_rows(doc, task_ids))
