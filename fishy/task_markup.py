"""Turning checkbox lines in evidence entries into tasks (spec §4.9). Pure text.

In the entry editor, *Convert to task* rewrites ``- [ ] text`` to
``- [[task:new]] text`` (``[[task:new:x]]`` for a checked box). Nothing is
created until the entry is saved; then the document creates one task per pending
line and `finalize_pending` swaps each line for ``- [[task:<id>]]``.
"""

from __future__ import annotations

import re

CHECKBOX_LINE = re.compile(r"^(\s*)([-*+])\s+\[( |x|X)\]\s+(.+)$")
PENDING_LINE = re.compile(r"^(\s*)([-*+]) \[\[task:new(:x)?\]\] (.+)$", re.M)


def convert_checkboxes(text: str, start: int, end: int) -> tuple[str, int]:
    """Rewrite each checkbox line overlapping ``[start, end]``; return (text, count)."""
    start, end = sorted((start, end))
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = count = 0
    for line in lines:
        line_end = pos + len(line)
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        m = CHECKBOX_LINE.match(body)
        if m and pos <= end and start <= pos + len(body):
            indent, bullet, mark, rest = m.groups()
            marker = "[[task:new:x]]" if mark in "xX" else "[[task:new]]"
            line = f"{indent}{bullet} {marker} {rest}{newline}"
            count += 1
        out.append(line)
        pos = line_end
    return "".join(out), count


def pending_tasks(md: str) -> list[tuple[str, bool]]:
    """(title, checked) for each pending line, in order."""
    return [(m.group(4), bool(m.group(3))) for m in PENDING_LINE.finditer(md or "")]


def finalize_pending(md: str, ids: list[int]) -> str:
    """Replace the n-th pending line with ``{indent}{bullet} [[task:{ids[n]}]]``."""
    it = iter(ids)
    return PENDING_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)} [[task:{next(it)}]]", md)
