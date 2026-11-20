# fishy

Root-cause analysis with an auto-generated, always-in-sync fishbone (Ishikawa) diagram.
See [requirements.md](requirements.md) for the full specification.

## Status

- **Milestone 1, layout spike: done.** A pure-Python layout engine plus a Qt renderer.
- **Milestone 2, data core: done.** SQLite project files with every change written
  immediately, schema migrations, undo/redo, the tree model, user stamps and history.
- **Milestone 3, editor + sync: done.** A keyboard-first tree-grid with dropdowns,
  drag-and-drop, a details panel, and diagram ↔ editor selection sync. Panes can be
  maximized, and the window layout is remembered between sessions.
- **Milestone 4, levels, statuses & legend: done.** A Levels & statuses editor, a diagram
  legend (included in exports), pin markers and category progress rings. Adds schema v2;
  older project files upgrade automatically, with a backup.
- **Milestone 5, evidence log: done.** Markdown entries with live preview, syntax-highlighted
  code, tables and task lists, pasted or dropped images stored in the project, bone
  cross-references, manual or date order, collapsible entries, and a project-wide timeline.
- **Milestone 6, export: done.** PNG, SVG and PDF with a live preview, an optional title
  block, legend and status toggles, PDF page size and orientation, "fully expanded", copy to
  clipboard, plus the bone outline as text and the whole project as JSON.
- **Milestone 7, polish: done.** Filter by text, level and status (the diagram fades the
  rest); light and dark themes; a Settings dialog (your name, theme, new-project defaults);
  recent projects; and a warning when a project is already open somewhere else.
- **Milestone 8, tasks: done.** Action items as real records: configurable task states with
  their own checkbox symbols, links to any number of bones, `[[task:N]]` references in entries,
  *Convert to task* for existing checkboxes, a Tasks section per bone, a sortable and editable
  Tasks window, and CSV export. Adds schema v3 (older files upgrade with a backup).

All v1 milestones are complete. Next candidates (see requirements §1.2): packaging as an
installer, a full investigation report, status roll-up rules.

## Setup

```powershell
uv sync                         # creates .venv with Python 3.15, PySide6 and dev tools
uv run main.py                  # the app; reopens your last project
uv run main.py path\to\x.fishy  # open a specific project
uv run python -m fishy.demo     # the milestone 1 layout demo (sample trees, export)
uv run pytest                   # tests
uv run ruff check .             # lint
```

Use **New sample…** to get a pre-filled investigation. There is no save button: every
edit is in the file as soon as you make it.

### Editing

Keys in the tree (when not typing in a cell):

| Key | Action |
|---|---|
| Enter | New sibling below, ready to type its title |
| Tab | New child, ready to type its title |
| Space / F2 | Rename |
| ← / → | Outdent / indent |
| Ctrl+↑ / Ctrl+↓ | Move up / down (Alt+↑ / Alt+↓ also work) |
| Shift+Tab | Outdent |
| Delete | Delete (asks if it has sub-causes or evidence) |
| + / − | Expand / collapse |

Window-wide shortcuts: Ctrl+Z / Ctrl+Y undo/redo · Ctrl+F find · Ctrl+Shift+F filter ·
Ctrl+1 diagram only · Ctrl+2 editor only · Ctrl+3 both · Ctrl+0 fit diagram ·
Ctrl+E zoom to selection · Ctrl+Shift+E export · Ctrl+Shift+C copy diagram image ·
Ctrl+, settings.

Other ways to edit:
- Double-click the Level or Status cell to get a named, color-coded dropdown. Choosing a
  level for a rolled-up parent pins it.
- Drag rows to reorder or reparent them. Dropping on a row makes the dragged bone its
  last child.
- Right-click a row or a bone in the diagram for level, status, pin, add, collapse in
  diagram, and delete.
- Double-click a bone in the diagram to jump to its title in the details panel.
- Moves that turn a cause into a category (or the reverse) ask first.

### Evidence

Select a cause and use **Add entry** in the details panel. Entries are Markdown, with a live
preview beside the editor:

- **Text:** bold, italic, `code`, lists, `- [ ]` task lists, tables, and fenced code blocks with
  syntax highlighting (`` ```python ``, `` ```sql ``, …).
- **Images:** paste a screenshot (Ctrl+V), drag image files in, or use **Insert image…**. Images
  are stored inside the `.fishy` file. Click one in an entry to see it full size or save it.
- **Links to bones:** **Link bone…** inserts `[[bone:N]]`, which always shows the bone's current
  number and title. Click it to jump there.
- **Links to tasks:** **Link task…** inserts `[[task:N]]`, shown as the task's live checkbox and
  title. Click the checkbox to toggle it, right-click for every state, click the title to edit
  the task.
- **Convert to task:** put the cursor on a `- [ ]` line (or select several) and press
  **Convert to task**. When you save, each becomes a task linked to this cause and the line
  becomes its reference; nothing else in the entry changes. `- [x]` lines become done tasks.
- Ctrl+Enter saves. Cancelling discards any images or pending tasks you added.

Each entry is a card showing its author and time (and who last edited it). Cards can be
collapsed to a single line, reordered with ▲/▼ in **Manual order**, or shown **By date**.
**Timeline** (Ctrl+T) lists every entry in the project in date order; double-click one to jump
to it.

### Tasks

Each bone's details panel has a **Tasks** section: **+ Add** creates a task linked to the bone,
**Link existing…** attaches one from elsewhere. A task can link to any number of bones; a
category also lists the tasks of its causes, grouped by cause. Click a checkbox to toggle
done, right-click it for every state, Ctrl+Shift+Enter cycles states, double-click to edit
(title, state, assignee, due date, critical, notes, linked bones, and the entries that
reference it). Done tasks are hidden until you press **Show done**. Overdue dates are red;
⚑ marks critical tasks. The grid's **Tasks** column shows each bone's open count.

**Tasks** (Ctrl+Shift+T) opens every task in one window: sort by any column, edit in place,
filter by text, assignee, critical, overdue or unlinked, and **Export CSV…** (the rows shown).
**Export ▾ → Export tasks (CSV)…** exports all of them. Deleting a bone keeps its tasks, unlinked.
Task states (names, colors, checkbox symbols, which ones count as done) are edited on the
**Task states** tab of **Levels & statuses…**.

### Levels, statuses and the legend

- **Levels & statuses…** (second toolbar) edits names, colors, definitions, order and
  defaults. Level order is severity (top = most severe), which drives roll-up. Deleting a
  level or status that's in use asks where to move its causes. Each status has a fixed
  meaning (not started / active / blocked / review / done) used for progress summaries.
  "Use these levels for new projects" saves your level set as the starting point for new
  projects.
- **Legend**, **position** and **Status dots** toggles control what the diagram and its exports
  show. They're saved per project.
- On the diagram, a pin marks a parent whose level overrides roll-up, and each category box
  shows a progress ring of its completed causes.

### Theme

**Theme** (second toolbar) is System, Light or Dark, remembered between sessions. System
follows the Windows app mode and changes with it. The diagram canvas and legend stay white
in both modes, like paper, so they always match the exported image and the level colors keep
their meaning. All UI colors live in `fishy/theme.py`; widgets opt in with properties
(`theme.mark(label, muted=True)`) rather than hardcoding colors.

### Export

**Export** (first toolbar, Ctrl+Shift+E) opens the diagram export dialog; the arrow beside it
has the rest.

- **Diagram:** PNG (1×–4×; the pixel size is shown), SVG or PDF (Letter, Legal, Tabloid, A4 or
  A3; auto, landscape or portrait; one page, scaled to fit and centered). The preview shows
  exactly what will be written, including the page for PDFs. Contents: title block (title,
  problem statement, date, author), legend, status dots and key, and *fully expanded* to
  include collapsed bones. Exports are drawn off-screen, so they never include the selection
  or the current zoom, and are always on white. Choices are remembered.
- **Copy diagram image** (Ctrl+Shift+C) copies a 2× image with the last-used title options,
  ready to paste into email, Word or PowerPoint.
- **Export outline / Copy outline:** the bones as a tab-indented bulleted list with each
  one's level and status, for pasting into reports.
- **Export project (JSON):** everything in the project, including evidence and images (as
  base64), plus computed numbers and rolled-up levels. For interchange and backup; v1 has no
  import.

### Filter

**Filter** (first toolbar, Ctrl+Shift+F) shows only bones with chosen levels, statuses, or
text in their title, number or description. Any ticked item in a list matches; text, levels
and statuses must all match. Levels are the ones shown on the diagram, so rolled-up levels
count; categories have no status, so a status filter shows them only as context.

Matches show normally and their parents stay visible in grey; everything else is hidden in
the grid and faded on the diagram. The filter follows your edits (set a bone to Blocked and it
appears), the bone you're working on stays visible until you move off it, and **✕** clears
it. Exports ignore the filter, and it isn't saved with the project.

### Settings, recent projects, and files open twice

- **Settings…** (Ctrl+,): your name (stamped on your changes; defaults to your Windows
  account), theme, and for new projects the category template, diagram style, and whether
  to use your saved level set or the built-in one. These are settings for this computer,
  not the project.
- **Open ▾** lists the last 8 projects; missing ones are greyed out.
- While a project is open, fishy keeps a hidden `name.fishy.lock` file beside it. Opening
  the same project in a second window, a second copy of fishy, or from another computer on
  a shared drive warns you first, and you can still open it anyway. A lock left by a crash is
  cleared automatically on the same computer.

## Code layout

```
fishy/
  domain/              # dataclasses + pure rules: numbering, level roll-up, status summaries,
                       #   tasks.py (open/overdue, toggle/cycle, category task roll-up)
  storage/
    schema/            # 001_init.sql (v1), 002_display_settings.sql (v2), 003_tasks.sql (v3)
    db.py              # connect, migrate (with backup), integrity checks
    repository.py      # the only code that touches SQL; transactions, stamps, history
  document.py          # ProjectDocument: the single mutation path; undo commands
  models/
    bone_tree_model.py # QAbstractItemModel over the document's caches
    task_table_model.py  # Tasks window table model + filter/sort proxy
  layout/              # pure Python, no Qt: tree in -> positioned lines/labels out
  views/
    diagram_view.py    # QGraphicsScene/View renderer: zoom, pan, select, export
    bone_tree_view.py  # tree-grid: keyboard map, level/status dropdown delegates
    details_panel.py   # selected bone: title, level + pin, status, description, stamps
    choices_dialog.py  # levels & statuses editor, level templates
    legend.py          # diagram legend (levels + definitions, status key)
    evidence.py        # evidence log cards, entry editor, image viewer, timeline
    markdown_view.py   # QTextBrowser that renders entries and loads project images
    tasks.py           # task state menu, pickers, task editor, per-bone section, Tasks window
  markdown_render.py   # Markdown -> HTML (tables, task lists, Pygments, bone/task links); no Qt
  task_markup.py       # "Convert to task": checkbox lines -> pending -> [[task:N]]; no Qt
  theme.py             # light/dark palettes + stylesheet; follows the OS setting
  filtering.py         # filter rules (text / level / status), no Qt
  project_lock.py      # "already open elsewhere" lock file (FR-7.7)
  export/
    diagram.py         # off-screen export scene, title block, PNG/SVG/PDF/clipboard rendering
    data.py            # outline text, project JSON, tasks CSV
  views/export_dialog.py  # export dialog with live preview
  views/filter_panel.py   # filter drop-down
  views/settings_dialog.py  # Settings (UI-8)
  app.py               # main window: panes, toolbars, selection sync, context menus
  demo.py              # milestone 1 layout demo
  samples.py           # sample investigations
tests/                 # 242 tests: layout geometry, storage + migrations, undo fuzzing,
                       #   Qt model checks, keyboard editing, drag-and-drop, details panel,
                       #   levels & statuses editor, legend, exports, markdown, evidence,
                       #   theme contrast and live switching, exports, filter, settings,
                       #   recent projects, locking, tasks
```

### Data flow

```
UI action ─► ProjectDocument method ─► Repository (one SQL transaction, on disk)
                    │                          │ (rule violation → error signal, no change)
                    ▼                          ▼
            QUndoStack command        caches reloaded ─► BoneTreeModel notifications
                                                      └► changed signal ─► diagram relayout
```

Undo and redo re-run repository calls, so every undo step is written to the file too.
Deletes return snapshots that restore the same ids, timestamps, log entries and
attachments.

### How the layout works

Bones alternate orientation by depth: categories are slanted, their causes are horizontal,
the next level is slanted again, and so on. The engine applies a shear transform
(`u = x + y·cot θ`) that makes every slanted bone vertical. That turns the fishbone into an
orthogonal tree, where each subtree is a list of axis-aligned boxes. Siblings are packed
by sliding them together until their boxes are `sibling_gap` apart. Shearing is
reversible, so boxes that don't overlap in sheared space mean shapes that don't overlap
on screen. The top side is laid out once and mirrored for the bottom. Text is measured
with real `QFontMetricsF` in the app, so the space reserved for each label matches what
is drawn.
