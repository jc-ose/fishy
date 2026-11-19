"""Markdown → HTML for evidence log entries (FR-5.2), tuned for Qt's QTextBrowser.

Pure Python (no Qt), so it is easy to test. Supports CommonMark plus GitHub-style
tables, task lists and strikethrough, and fenced code blocks with Pygments
syntax highlighting. Raw HTML in the source is disabled, so pasted text can't
inject markup.

Two fishy-specific link forms:

* ``![caption](attachment:42)`` — an image stored in the project (FR-5.3).
  Rendered as a clickable preview; the viewer loads the bytes by id.
* ``[[bone:12]]`` — a cross-reference to a bone by its stable id (FR-1.3).
  Rendered with the bone's *current* number and title, so it stays correct
  when bones are renumbered.
* ``[[task:14]]`` — a task by its stable id: its state symbol (a
  ``fishy-task-toggle:`` link, in the state's color) and its current title (a
  ``fishy-task:`` link, struck through when done). ``[[task:new]]`` /
  ``[[task:new:x]]`` mark a checkbox line waiting to become a task when its entry
  is saved. Both are parsed by an inline rule, so they stay literal inside code.

QTextBrowser understands only a subset of HTML/CSS, so the output uses inline
styles and table attributes rather than stylesheets for anything that matters.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.util import ClassNotFound

ATTACHMENT_SCHEME = "attachment"
BONE_SCHEME = "fishy-bone"
BONE_REF = re.compile(r"\[\[bone:(\d+)\]\]")
TASK_SCHEME = "fishy-task"
TASK_TOGGLE_SCHEME = "fishy-task-toggle"
TASK_REF = re.compile(r"\[\[task:(\d+)\]\]")
_TASK_TOKEN = re.compile(r"\[\[task:(?:(\d+)|new(:x)?)\]\]")
ATTACHMENT_REF = re.compile(r"!\[([^\]]*)\]\(attachment:(\d+)\)")


@dataclass(frozen=True)
class MarkdownColors:
    """Colors for rendered entries. Text itself inherits the widget's palette."""

    code_bg: str
    inline_code_bg: str
    table_border: str
    header_bg: str
    link: str
    quote: str
    pygments_style: str


LIGHT_COLORS = MarkdownColors(code_bg="#f6f8fa", inline_code_bg="#eef1f4",
                              table_border="#d0d7de", header_bg="#f6f8fa",
                              link="#0969da", quote="#57606a", pygments_style="default")
DARK_COLORS = MarkdownColors(code_bg="#1b1e22", inline_code_bg="#363b42",
                             table_border="#4a5058", header_bg="#30353b",
                             link="#6cb6ff", quote="#a3adb6", pygments_style="github-dark")


def colors_for(dark: bool) -> MarkdownColors:
    return DARK_COLORS if dark else LIGHT_COLORS


def stylesheet(dark: bool = False) -> str:
    """Defaults applied to every rendered entry (QTextDocument supports this subset)."""
    c = colors_for(dark)
    return f"""
code {{ font-family: Consolas, 'Cascadia Mono', monospace; background-color: {c.inline_code_bg}; }}
pre {{ font-family: Consolas, 'Cascadia Mono', monospace; margin: 0; }}
blockquote {{ color: {c.quote}; margin-left: 10px; }}
th {{ background-color: {c.header_bg}; }}
a {{ color: {c.link}; }}
h1 {{ font-size: 15pt; }} h2 {{ font-size: 13pt; }} h3 {{ font-size: 11.5pt; }}
"""


STYLESHEET = stylesheet(False)

BoneLabel = Callable[[int], str | None]  # bone id -> "1.2 Title" (None if deleted)


@dataclass(frozen=True)
class TaskInfo:
    """What a rendered task reference needs to know about the task."""

    symbol: str
    color: str
    title: str
    done: bool


TaskLabel = Callable[[int], TaskInfo | None]  # task id -> info (None if deleted)

_formatters: dict[str, HtmlFormatter] = {}


def _formatter(style: str) -> HtmlFormatter:
    if style not in _formatters:
        _formatters[style] = HtmlFormatter(noclasses=True, nowrap=True, style=style)
    return _formatters[style]


def highlight_code(code: str, lang: str, dark: bool = False) -> str:
    """A fenced code block as a shaded table (QTextBrowser ignores <pre> backgrounds)."""
    c = colors_for(dark)
    try:
        lexer = get_lexer_by_name(lang.strip()) if lang.strip() else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    body = pygments_highlight(code, lexer, _formatter(c.pygments_style)).rstrip("\n")
    return (f'<table width="100%" cellpadding="8" cellspacing="0" bgcolor="{c.code_bg}">'
            f'<tr><td><pre>{body}</pre></td></tr></table>\n')


def _render_image(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    src = token.attrGet("src") or ""
    alt = html.escape(self.renderInlineAsText(token.children or [], options, env))
    src_attr = html.escape(src, quote=True)
    if src.startswith(f"{ATTACHMENT_SCHEME}:"):
        title = f' title="{alt}"' if alt else ""
        return f'<a href="{src_attr}"><img src="{src_attr}" alt="{alt}"{title}></a>'
    # QTextBrowser can't fetch web images; show a link instead.
    return f'<a href="{src_attr}">🖼 {alt or src_attr}</a>'


def _render_fence(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    lang = (token.info or "").strip().split(" ")[0]
    return highlight_code(token.content, lang, env.get("dark", False))


def _render_code_block(self, tokens, idx, options, env) -> str:  # indented code
    return highlight_code(tokens[idx].content, "", env.get("dark", False))


def _task_ref_rule(state, silent: bool) -> bool:
    if not state.src.startswith("[[task:", state.pos):
        return False
    m = _TASK_TOKEN.match(state.src, state.pos)
    if m is None:
        return False
    if not silent:
        token = state.push("task_ref", "", 0)
        token.meta = {"id": int(m.group(1)) if m.group(1) else None,
                      "checked": bool(m.group(2))}
    state.pos = m.end()
    return True


def _render_task_ref(self, tokens, idx, options, env) -> str:
    meta = tokens[idx].meta
    if meta["id"] is None:  # pending conversion, shown in the editor preview
        todo, done = env.get("pending_symbols", ("☐", "☑"))
        return f'<span style="color: #888888;">(new)</span> {done if meta["checked"] else todo}'
    task_id = meta["id"]
    label = env.get("task_label")
    info = label(task_id) if label else None
    if info is None:
        return f"<s>deleted task #{task_id}</s>"
    symbol = (f'<a href="{TASK_TOGGLE_SCHEME}:{task_id}" style="color: {html.escape(info.color)}; '
              f'text-decoration: none;">{html.escape(info.symbol)}</a>')
    title = f'<a href="{TASK_SCHEME}:{task_id}">{html.escape(info.title)}</a>'
    return f"{symbol} {'<s>' + title + '</s>' if info.done else title}"


def _make_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False})
    md.enable(["table", "strikethrough"])
    md.use(tasklists_plugin)
    md.add_render_rule("image", _render_image)
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_code_block)
    md.inline.ruler.before("link", "task_ref", _task_ref_rule)
    md.add_render_rule("task_ref", _render_task_ref)
    return md


_parser = _make_parser()

_CHECKBOX = re.compile(r"<input[^>]*type=\"checkbox\"[^>]*>\s?")


def _escape_link_text(text: str) -> str:
    return re.sub(r"([\[\]\\*_`])", r"\\\1", text)


def expand_bone_refs(md: str, bone_label: BoneLabel | None) -> str:
    def repl(m: re.Match) -> str:
        bone_id = int(m.group(1))
        label = bone_label(bone_id) if bone_label else None
        if label is None:
            return f"~~deleted bone #{bone_id}~~"
        return f"[{_escape_link_text(label)}]({BONE_SCHEME}:{bone_id})"

    return BONE_REF.sub(repl, md)


def render(md: str, bone_label: BoneLabel | None = None, dark: bool = False,
           task_label: TaskLabel | None = None,
           pending_symbols: tuple[str, str] = ("☐", "☑")) -> str:
    """Render entry markdown to QTextBrowser-friendly HTML."""
    env = {"dark": dark, "task_label": task_label, "pending_symbols": pending_symbols}
    out = _parser.render(expand_bone_refs(md or "", bone_label), env)
    out = _CHECKBOX.sub(lambda m: "☑ " if "checked" in m.group(0) else "☐ ", out)
    # The checkbox replaces the bullet in task lists.
    out = out.replace('<ul class="contains-task-list">',
                      '<ul class="contains-task-list" style="list-style-type: none;">')
    out = out.replace("<table>", f'<table border="1" cellspacing="0" cellpadding="5" '
                                 f'style="border-color: {colors_for(dark).table_border};">')
    return out


def _task_title(task_label: TaskLabel | None, m: re.Match) -> str:
    if m.group(1) is None:
        return ""
    info = task_label(int(m.group(1))) if task_label else None
    return info.title if info else f"task #{m.group(1)}"


def summary_line(md: str, bone_label: BoneLabel | None = None, limit: int = 140,
                 task_label: TaskLabel | None = None) -> str:
    """First meaningful line as plain text (collapsed entries, timeline, FR-5.1b)."""
    text = ATTACHMENT_REF.sub(lambda m: f"[image: {m.group(1) or 'image'}]", md or "")
    text = _TASK_TOKEN.sub(lambda m: _task_title(task_label, m), text)
    text = BONE_REF.sub(lambda m: (bone_label(int(m.group(1))) if bone_label else None)
                        or f"bone #{m.group(1)}", text)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```") or set(line) <= set("-|: "):
            continue
        line = re.sub(r"^(#+|[-*+]|\d+\.|>)\s+", "", line)
        line = re.sub(r"^\[[ xX]\]\s+", "", line)
        line = re.sub(r"[*_`~]", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def task_ids(md: str) -> list[int]:
    """Ids of the tasks referenced by an entry, distinct, in order of appearance."""
    return list(dict.fromkeys(int(m.group(1)) for m in TASK_REF.finditer(md or "")))


def attachment_ids(md: str) -> list[int]:
    """Ids of project images referenced by an entry, in order."""
    return [int(m.group(2)) for m in ATTACHMENT_REF.finditer(md or "")]
