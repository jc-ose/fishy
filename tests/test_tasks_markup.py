"""Tasks: rendering task references and converting checkbox lines (pure)."""

from __future__ import annotations

from fishy.markdown_render import TaskInfo, render, summary_line, task_ids
from fishy.task_markup import convert_checkboxes, finalize_pending, pending_tasks

INFO = {14: TaskInfo("◐", "#1e88e5", "Pull records", False),
        15: TaskInfo("☑", "#43a047", "Call vendor", True)}


def test_task_ref_renders_symbol_and_title_links():
    out = render("- [[task:14]]\n- [[task:15]]\n- [[task:99]]", task_label=INFO.get)
    assert ('<a href="fishy-task-toggle:14" style="color: #1e88e5; text-decoration: none;">◐</a>'
            in out)
    assert '<a href="fishy-task:14">Pull records</a>' in out
    assert '<s><a href="fishy-task:15">Call vendor</a></s>' in out
    assert "<s>deleted task #99</s>" in out


def test_pending_task_preview():
    out = render("- [[task:new]] Pull records\n- [[task:new:x]] Done one")
    assert "☐ Pull records" in out and "☑ Done one" in out and "(new)" in out


def test_task_ref_cannot_inject_html():
    out = render("[[task:14]]", task_label=lambda _: TaskInfo("☐", "#000", "<b>x</b>", False))
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out


def test_task_ref_inside_code_is_left_alone():
    out = render("`[[task:14]]`", task_label=INFO.get)
    assert "fishy-task" not in out


def test_summary_and_ids():
    assert summary_line("- [[task:14]] then", task_label=INFO.get) == "Pull records then"
    assert summary_line("- [[task:new]] Fresh one") == "Fresh one"
    assert task_ids("[[task:3]] [[task:1]] [[task:3]]") == [3, 1]


def test_convert_cursor_line_only():
    text = "intro\n- [ ] Pull records\n- [x] Done thing\n- plain\n"
    new, n = convert_checkboxes(text, text.index("Pull"), text.index("Pull"))
    assert n == 1 and new == "intro\n- [[task:new]] Pull records\n- [x] Done thing\n- plain\n"


def test_convert_selection_and_checked():
    text = "- [ ] a\n- [X] b\nnot a box\n"
    new, n = convert_checkboxes(text, 0, len(text))
    assert n == 2 and new == "- [[task:new]] a\n- [[task:new:x]] b\nnot a box\n"


def test_convert_nothing_qualifies():
    assert convert_checkboxes("plain\n", 0, 3) == ("plain\n", 0)


def test_convert_preserves_odd_lines():
    text = "- parent\n    * [ ] **Bold** see [[bone:3]] `x`  \n- [ ] keep\n"
    pos = text.index("Bold")
    new, n = convert_checkboxes(text, pos, pos)
    assert n == 1
    assert new == "- parent\n    * [[task:new]] **Bold** see [[bone:3]] `x`  \n- [ ] keep\n"
    assert pending_tasks(new) == [("**Bold** see [[bone:3]] `x`  ", False)]


def test_finalize_pending():
    md = "x\n  - [[task:new]] a\n- [[task:new:x]] b\n"
    assert pending_tasks(md) == [("a", False), ("b", True)]
    assert finalize_pending(md, [7, 8]) == "x\n  - [[task:7]]\n- [[task:8]]\n"
