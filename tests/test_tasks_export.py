"""Tasks: CSV export (and JSON, from Task 12)."""

from __future__ import annotations

import csv
import io

from fishy.export.data import CSV_COLUMNS, write_tasks_csv


def test_csv_columns_and_bom(tmp_path, doc_with_cause):
    doc, cause = doc_with_cause
    doc.add_task("Pull records", [cause], assignee="J. Smith", due_date="2026-11-24")
    p = tmp_path / "t.csv"
    write_tasks_csv(doc, p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"), newline="")))
    assert rows[0] == CSV_COLUMNS == [
        "ID", "State", "Done", "Title", "Bones", "Assignee", "Due", "Critical", "Overdue",
        "Notes", "Created", "Created by", "Updated", "Updated by"]
    assert rows[1][1:4] == ["To do", "no", "Pull records"]
    assert rows[1][4] == "1.1 c" and rows[1][5] == "J. Smith" and rows[1][6] == "2026-11-24"
    assert rows[1][11] == "T"


def test_csv_round_trips_awkward_text(tmp_path, doc):
    t = doc.add_task("Café ☑ “quoted”", notes='a, "b"\nsecond line')
    p = tmp_path / "t.csv"
    write_tasks_csv(doc, p, [t])
    rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"), newline="")))
    assert rows[1][3] == "Café ☑ “quoted”" and rows[1][9] == 'a, "b"\nsecond line'


def test_csv_respects_given_order(tmp_path, doc):
    a = doc.add_task("a")
    b = doc.add_task("b")
    p = tmp_path / "t.csv"
    write_tasks_csv(doc, p, [b, a])
    rows = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8-sig"), newline="")))
    assert [r[0] for r in rows[1:]] == [str(b), str(a)]


def test_json_includes_tasks(doc):
    from fishy.export.data import project_dict

    c = doc.add_bone(doc.tree.roots()[0], "c")
    t = doc.add_task("x", [c], critical=True)
    d = project_dict(doc, include_images=False)
    assert d["tasks"][0]["id"] == t and d["tasks"][0]["bone_ids"] == [c]
    assert d["tasks"][0]["critical"] is True and d["tasks"][0]["state"] == "To do"
    assert len(d["task_states"]) == 4 and d["task_states"][3]["role"] == "done"
    assert d["bones"][0]["children"][0]["task_ids"] == [t]
    assert d["bones"][0]["task_ids"] == []
