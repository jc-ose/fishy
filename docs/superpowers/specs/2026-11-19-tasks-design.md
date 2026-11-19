# Tasks — design

> Status: **approved** 2026-11-19 · targets schema v3

## 1. Intent

Action items are currently written as `- [ ]` lines inside evidence log entries. In a
project with many bones and many entries per bone they get lost: nobody can see every open
action, who owns it, or when it's due. Tasks become first-class records with their own
stable ids. Each bone shows its tasks, and one window lists every task in the project for
tracking and follow-up meetings.

**Success:** at any point the team can open one list, sort it by due date or assignee,
update states in place, and export it to CSV. No action item lives only as text in a
log entry.

## 2. Goals, non-goals, anti-goals

**Goals**
- A task is its own entity with a stable id. It can link to **zero or more** bones
  (categories or causes).
- Fields: title, notes, state, assignee, due date, critical flag, plus the usual timestamp
  and user stamps.
- Task states can be configured in the Levels & statuses dialog. Each state has its own
  symbol and color, so the checkbox looks different per state.
- Tasks can be referenced from log entries with `[[task:N]]`, the same way bones are.
- A **Convert to task** action turns an existing `- [ ]` / `- [x]` line into a real
  task without disturbing the rest of the entry.
- A per-bone **Tasks** section in the details panel.
- A separate, non-modal **Tasks window** that lists every task. It is sortable, filterable
  and editable in place.
- **CSV export** of tasks.

**Non-goals (this pass)**
- No change to the diagram (no task badges or markers).
- No change to the bone filter (UI-7). It doesn't gain "has open tasks".
- No automatic scan or conversion of checkboxes in existing projects. Users convert
  them line by line.
- Task states don't drive bone status (that stays manual, FR-3a.7).
- No reminders or notifications, no recurring tasks, no sub-tasks.
- No managed people list. The assignee is free text (§4.3).

**Anti-goals** (failure even if it "works")
- Converting a checkbox must never reflow, reorder or otherwise alter any other part of the
  entry.
- Deleting a bone must never delete a task.
- No task edit may bypass the command layer. Every change is undoable and written through
  immediately, like everything else in fishy.

## 3. Constraints

- Follow the existing architecture: UI → `ProjectDocument` command (`QUndoCommand`) →
  `Repository` SQL transaction → signals → views. `Repository` remains the only code
  that touches SQL.
- Schema change goes through a new migration, `003_tasks.sql`. Older files are backed up
  (`.bak-v2`) and upgraded on open, as in milestone 4.
- Ids use `AUTOINCREMENT` so they are never reused (`[[task:N]]` references must not
  break).
- `markdown_render.py` stays pure Python with no Qt dependency.
- Raw HTML is still disabled in entries. Task rendering uses only the link and inline-style
  forms QTextBrowser already handles.
- Windows-first, offline, Qt Widgets (unchanged).

## 4. Design

### 4.1 Schema (v3, `003_tasks.sql`)

```sql
CREATE TABLE task_state (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    symbol      TEXT NOT NULL,             -- one glyph drawn as the checkbox, e.g. ☐ ◐ ⊘ ☑
    role        TEXT NOT NULL DEFAULT 'not_started'
                CHECK (role IN ('not_started', 'active', 'blocked', 'review', 'done')),
    sort_order  INTEGER NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES user(id), updated_by INTEGER REFERENCES user(id)
);

CREATE TABLE task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',  -- plain text
    state_id    INTEGER NOT NULL REFERENCES task_state(id),
    assignee    TEXT,                      -- free text, NULL = unassigned
    due_date    TEXT,                      -- ISO date 'YYYY-MM-DD' (no time), NULL = none
    critical    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    created_by INTEGER REFERENCES user(id), updated_by INTEGER REFERENCES user(id)
);

CREATE TABLE task_bone (                   -- many-to-many
    task_id    INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    bone_id    INTEGER NOT NULL REFERENCES bone(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES user(id),
    PRIMARY KEY (task_id, bone_id)
);
CREATE INDEX task_bone_bone ON task_bone(bone_id);
```

The migration seeds the default states:

| Order | Name | Symbol | Color | Role | Default |
|---|---|---|---|---|---|
| 0 | To do | ☐ | grey | not_started | ✓ |
| 1 | In progress | ◐ | blue | active | |
| 2 | Blocked | ⊘ | red | blocked | |
| 3 | Done | ☑ | green | done | |

Domain: add `TaskState` (it reuses `StatusRole`) and `Task` dataclasses in
`domain/model.py`. A `Task` carries `bone_ids: list[int]`.

**Derived values (computed, never stored):**
- *open*: the state's role is not `done`.
- *overdue*: open, and `due_date` is earlier than today's local date.

### 4.2 Task states (configuration)

- A third tab, **Task states**, in the Levels & statuses dialog. It reuses `_ChoiceTab`,
  generalised to a third kind. The fields are name, color, **symbol** (a short text field
  with a small picker of suggested glyphs), role, default, and order. "Order" sets the
  cycle order.
- Same rules as statuses. Deleting a state that tasks use requires reassigning them.
  Exactly one state is the default.
- **At least one state must have the `done` role.** Deleting or re-roling the last one is
  refused, because the click-to-toggle behaviour depends on it.

### 4.3 Assignee

Free text. Editors offer a completer (an editable combo box) listing the distinct
non-empty assignees already used in the project, sorted case-insensitively. No people
table. Whitespace is trimmed, and an empty value means unassigned.

### 4.4 Interacting with a task's checkbox (Trilium-style)

Everywhere a task's checkbox appears (the per-bone list, the Tasks window, a rendered
`[[task:N]]`):
- **Left-click** toggles the task. If it's open, it moves to the first `done`-role state
  (by sort order). If it's done, it moves to the default state.
- **Right-click** opens a menu of every state (symbol, name), with the current one
  checked.
- **Ctrl+Shift+Enter** (in the per-bone list and the Tasks window) moves the selected
  task to the next state in sort order, wrapping around.
- A done task's title is drawn struck through and muted.

### 4.5 Per-bone Tasks section (details panel)

It sits between the description and the evidence log, and appears for categories and
causes alike.

```
Tasks (3 open)                                   [+ Add] [Link existing…]
 ☐  Pull calibration records       J. Smith    Nov 24
 ◐  Interview night-shift operator  A. Lee     Nov 21   ⚑
 ⊘  Get vendor CoA                  —          Dec 02
```

- Each row shows the state symbol in the state's color, the title, the assignee, the due
  date (red when overdue) and ⚑ when the task is critical.
- **Done tasks are hidden by default.** A **Show done (N)** toggle in the section header
  brings them back, where N is the number of hidden done tasks. The toggle is an app
  setting (per computer, like the evidence log's order toggle), so it stays the same from
  bone to bone and across sessions. If you mark a task done while done tasks are hidden, its
  row fades out after a short moment instead of vanishing at once, so you can still undo
  or right-click it.
- **Order:** open tasks first, then done (when shown). Within each group, sorted by due
  date (no date last), then by id. There is no manual ordering.
- **Categories roll up.** On a category, the section lists the tasks linked directly to the
  category first, then a group for each cause (in tree order, labelled with its number and
  title) holding the tasks linked to that cause or any of its descendants. A task linked to
  several bones in the category appears **once**, under the first matching group in that
  order. The header count ("7 open") counts each task once across the whole category.
  Parent causes don't roll up: they show only their own direct links.
- **+ Add** creates a task linked to this bone and opens the task editor. On a category, it
  links the task to the category itself.
- **Link existing…** opens a searchable picker of tasks not yet linked to this bone.
- Double-click a row to open the task editor. The context menu offers *Edit…*,
  *Unlink from this bone*, *Delete task…* and the state submenu.
- The header count ("3 open") counts open tasks.

### 4.6 Task editor

A small dialog for one task:
- Title, state, assignee (with completer), due date (a date picker with a "No date"
  option), critical checkbox, notes (multi-line plain text).
- **Linked bones:** a list with *Add…* (the same picker as *Link bone…*) and *Remove*.
  Clicking a bone selects it in the main window.
- **Referenced in:** a read-only list of log entries whose body contains `[[task:N]]`,
  found by scanning bodies. Double-clicking one opens it, the way the Timeline does.
- Shows "Created by … · Updated by …".

Edits commit field by field through document commands, the same way the details panel
does.

### 4.7 Deleting

- **Deleting a task** asks for confirmation if it is referenced in any entry, and says
  how many entries reference it. It's undoable. References to a deleted task render as
  `~~deleted task #N~~`, the same way bones do.
- **Deleting a bone** removes only its `task_bone` rows (by cascade). The bone snapshot
  (`snapshot_subtree`) must include the `task_bone` rows for every bone in the subtree,
  and `restore_subtree` must put them back. Tasks themselves are never deleted with a
  bone. If that leaves a task with no links, it appears only in the Tasks window.

### 4.8 References in log entries: `[[task:N]]`

- `markdown_render.py` adds `TASK_REF = r"\[\[task:(\d+)\]\]"` and an expander that
  takes a `TaskLabel` callback (id → symbol, color, title, is_done, or None if deleted).
  The renderer has no Qt dependency.
- It renders as **two anchors**: the symbol, as `fishy-task-toggle:N` in the state's color,
  then the title, as `fishy-task:N`, struck through when done.
- In `MarkdownView`, clicking the symbol toggles the task (§4.4) and clicking the title
  opens the task editor. Right-clicking either anchor (via `anchorAt`) shows the state
  menu. Rendered entries refresh when a task changes.
- `summary_line` replaces a reference with the task's title.
- The entry editor gets a **Link task…** button next to *Link bone…*. It inserts
  `[[task:N]]`, picking from all tasks, with this bone's tasks listed first.

### 4.9 Convert to task

A **Convert to task** button (and context-menu item) in the entry editor acts on the line
under the cursor, or on every line touched by the selection.

- A line qualifies if it matches `^(\s*)([-*+])\s+\[( |x|X)\]\s+(.+)$`. Other lines
  are skipped silently. If no line in the selection qualifies, the button does nothing and
  the status line says so.
- **In the editor**, the line is rewritten to
  `{indent}{bullet} [[task:new]] {original text}`. No task is created yet, so cancelling
  the editor leaves nothing behind. The preview renders `[[task:new]]` as a ☐ (or ☑ if
  the box was checked) followed by the text and a muted "(new)".
- **On Save**, the save command creates one task per `[[task:new]]` line, in a single
  undoable step together with the entry update:
  - title = the line's text, kept exactly as written;
  - state = the default state, or the first `done`-role state if the box was `[x]`;
  - linked to the entry's bone;
  - the line becomes `{indent}{bullet} [[task:<id>]]`.

  Undoing the save removes the tasks and restores the previous body. Redo recreates them
  with the same ids.
- The checked/unchecked flag for a pending conversion is kept as `[[task:new]]` vs
  `[[task:new:x]]`, so the editor text alone carries it.
- Nothing else in the body changes: no re-wrapping, no renumbering, no whitespace edits
  outside the converted lines.

### 4.10 Tasks window (all tasks)

A non-modal window modeled on `TimelineDialog`. It opens from a **Tasks** toolbar action
next to Timeline, with shortcut **Ctrl+Shift+T** (Ctrl+T is already Timeline). Only one
instance is open at a time, and it closes with the project.

- A `QTableView` over a task table model, with a `QSortFilterProxyModel` for sorting and
  filtering. (A proxy is fine here: this is a flat model separate from the bone tree.)
- **Columns:** `#` (id), State (symbol + name), Title, Bones, Assignee, Due, Critical,
  Updated. Every column sorts. Sorting by Bones uses the first bone's number in natural
  order (1.2 before 1.10). Sorting by State uses the state's sort order.
- **Editable in place:** state (combo box, plus the click and right-click behaviour of
  §4.4), title, assignee (editable combo with completer), due date (date editor that can
  be cleared), critical (checkbox). Bones shows the bone numbers ("1.2, 3.1"), with
  titles in a tooltip. Double-clicking that cell opens the task editor on its Linked bones
  list. Double-clicking any read-only cell opens the task editor.
- **Filters** (top bar): a text search over title, notes and assignee; *Hide done*
  (on by default); an assignee dropdown (All / Unassigned / each name); *Critical only*;
  *Overdue only*; *Unlinked only*.
- **Buttons:** *+ New task* (creates an unlinked task and opens the editor) and
  *Export CSV…*.
- A summary label reads "12 tasks · 7 open · 2 overdue · 1 critical", counting all tasks
  regardless of filters.
- Selecting a row with exactly one linked bone selects that bone in the main window.
- Updates live from document signals.

### 4.11 Bone grid column

Add a **Tasks** column to the tree-grid (UI-3), after "# Log entries". It shows the open
count ("2"), followed by ⚑ when any open task on the bone is critical, and is blank when
there are none. The tooltip lists the open task titles. It is read-only.

On **category** rows, the count rolls up: it counts each open task once, whether it's
linked to the category or to any bone beneath it (the same set as §4.5). Cause rows count
direct links only. Like the other roll-ups, these counts are computed, not stored.

### 4.12 CSV export

- **Export tasks (CSV)…** in the Export menu exports **all** tasks. *Export CSV…* in the
  Tasks window exports **the rows currently shown**, in their current sort order, so the
  filters double as a way to choose what to export.
- **Columns:** ID, State, Done (yes/no), Title, Bones (`1.2 Title; 3.1 Title`),
  Assignee, Due (YYYY-MM-DD), Critical (yes/no), Overdue (yes/no), Notes, Created,
  Created by, Updated, Updated by. Times are local and formatted as in the UI.
- UTF-8 **with BOM** and CRLF line endings, written with the stdlib `csv` module, so
  Excel opens the glyphs and accents correctly.
- Lives in `fishy/export/data.py` (`write_tasks_csv`) and is pure enough to test without a
  window.

### 4.13 JSON export, history, undo

- The JSON export (FR-8.5) gains `task_states` and `tasks` (each with `bone_ids`), and
  each bone gets `task_ids`.
- `history` records `task`, `task_state` and `task_link` actions (create, update,
  delete, link, unlink), with before and after values.
- Every operation is a `ProjectDocument` command, so it can be undone. A new
  `tasksChanged` signal fires after task changes. The details panel, the Tasks window, the
  grid column and the rendered entries all listen to it.

## 5. Approaches considered

| Decision | Options | Picked | Why |
|---|---|---|---|
| Existing log checkboxes | Leave as formatting / **Convert-to-task action** / auto-promote every checkbox | Convert action | Keeps the user in control. Nothing in an entry changes unless asked. Avoids flooding the list with throwaway checklist lines. |
| Assignee | **Free text with suggestions** / managed people list | Free text | Quick to use, fits outside contacts who never open the file. Accepted cost: name variants count as different people. |
| All-tasks view | **Separate non-modal window** / docked panel / tab swapping with the diagram | Window | Can stay open on a second monitor. Doesn't crowd the existing split view. Reuses the Timeline pattern. |
| Orphaned tasks | Delete with bone / **keep, unlinked** | Keep | User decision. They remain visible in the Tasks window. |

## 6. Decided by the user
- Tasks link to many bones. They have due date, assignee and critical flag, configurable
  states that change the checkbox look (Trilium-style), and `[[task:N]]` references.
  There is a sortable, editable all-tasks view.
- Tasks of a deleted bone remain, unlinked, visible only in the all-tasks view.
- Convert-to-task action for existing checkboxes, non-disruptive to content.
- Free-text assignee with suggestions.
- All-tasks view is a separate window, like the Timeline.
- Tasks are exportable to CSV.
- Categories roll up their causes' tasks, in both the grid column and the Tasks section
  (grouped by cause, each task listed once).
- Done tasks are hidden in the per-bone section, with a toggle to show them.
- Per-bone Tasks section, checkbox interactions, grid column, no diagram change: approved
  as proposed.

## 7. Designer's calls (check these)
- Default task states and glyphs (§4.1). There's no "Cancelled" state by default; users
  can add one.
- A done-role state is required (§4.2).
- The per-bone list is sorted automatically, with no manual order (§4.5).
- Conversion is deferred until Save via `[[task:new]]` (§4.9).
- Shortcut Ctrl+Shift+T (§4.10). *Hide done* is on by default.
- A CSV export from the window respects its filters; the one from the Export menu exports
  everything (§4.12).
- Notes are plain text, not markdown.
- Only categories roll up tasks. Parent causes show only their own direct links (§4.5).
- Due is a date only, with no time of day.

## 8. Left to the builder
- Widget structure and file layout. Suggested: `views/tasks.py` for the per-bone section,
  editor and window; `models/task_table_model.py`.
- Glyph picker contents. Font fallback if a glyph doesn't render in the UI font.
- How rendered entries get refreshed on `tasksChanged` (re-render everything, or only
  entries that reference the changed task).
- Requirements doc update: add §4.9 *Tasks* (FR-9.x), the schema notes in §10.1, and the
  UI-3 column.
- Tests should cover at least: the migration v2→v3; delete-bone/undo restoring links;
  convert-on-save with undo and redo restoring the same ids; toggle rules; overdue
  computation; CSV encoding and filtered export.
