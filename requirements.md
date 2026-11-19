# App: fishy (name may change)

> Status: **v1 complete (v0.12)** — v0.3 added decisions on Status, log entries, theming and layout;
> v0.4 added level roll-up, categories, user identity, sizing, platform and scope decisions;
> v0.5 settled category logs, level pinning and parent status (§11.1); v0.6 recorded
> implementation decisions from milestones 1–2 (§10.1); v0.7 added the agreed keyboard map
> (UI-6) and diagram reading order (FR-2.2); v0.8 recorded milestone 4 (schema v2, legend,
> markers); v0.9 records milestone 5 (evidence log; FR-1.3, §4.5, §10.1, §12); v0.10 records
> light/dark theming, done early (UI-1, §10.1); v0.11 records milestone 6 (export; §4.8, §10.1);
> v0.12 records milestone 7 (filter, settings, recent files, open-twice warning). Items marked
> **[Proposed]** are minor suggestions that can be confirmed during implementation.

---

## 1. Scope

fishy is a desktop app for running a root-cause investigation. It gives you a modern
interface for documenting causes and evidence, and it keeps a Fishbone (Ishikawa)
diagram generated and up to date automatically.

### 1.1 In scope (v1)
- One investigation per project file, holding one problem statement and one fishbone.
- A hierarchical editor for bones (causes), with per-bone details and an evidence log.
- An auto-laid-out fishbone diagram with a legend, click-to-navigate and style presets.
- Configurable cause levels (classification, color, definition) and investigation statuses (progress tracking).
- A project file in SQLite that is always in sync with the app (no "unsaved changes").
- Export of the diagram to PNG, SVG and PDF.

### 1.2 Out of scope (v1) — candidates for later
- Multi-user or real-time collaboration and network sync.
- Other RCA methods (5-Whys, fault tree). The data model should not rule them out.
- Free-form manual positioning of bones on the diagram (layout is always automatic).
- Report generation beyond diagram export (e.g., a full investigation report PDF). **Planned as a future focus**; the JSON export (FR-8.5) and the data model should make it straightforward.
- Importing from other tools (Excel, Visio, Minitab).
- Release-tested support for macOS/Linux.
- Cloud storage, accounts, permissions.

## 2. Description

The app provides two primary interfaces that always stay in sync:

1. **Diagram**: an auto-generated, auto-updated fishbone graphic with basic interactivity
   (select, navigate, zoom/pan, collapse).
2. **Data editor**: the main editing interface, with two functions:
   - a **hierarchical bone definition** interface (tree-grid);
   - a **per-bone details** panel: description, level, status, and an evidence log
     that can hold multiple entries.

## 3. Glossary

| Term | Meaning |
|---|---|
| **Project** | One investigation; one `.fishy` file on disk. |
| **Problem statement** | The effect, shown in the fish "head". |
| **Category** | A top-level bone attached directly to the spine (e.g., the 6Ms). Categories are a distinct kind of bone with their own behavior and styling (§4.1a). |
| **Bone** | A node in the hierarchy. Used generically for categories and causes; *cause* means a non-category bone. |
| **Severity** | The rank of a level, taken from its sort order (Root Cause highest, Ruled Out lowest). Used for level roll-up. |
| **User** | The person stamped on records as creator or last editor (FR-6.4). |
| **Number** | Section-style display number derived from position (`1`, `1.2`, `1.2.3`). |
| **Level** | Classification of a bone (Root Cause, Direct Cause, …). Drives its color. Configurable. |
| **Status** | Progress of the investigation of a bone: Not Started, In Progress, Blocked, Under Review, Complete. Separate from Level, and does not drive bone color. |
| **Log entry** | A timestamped markdown section of the investigation under a bone (findings, evidence, analysis). Read in order, entries can also serve as a progress history. |
| **Attachment** | An image or file referenced by a log entry, stored inside the project file. |

## 4. Functional Requirements

### 4.1 Bones and hierarchy
- **FR-1.1** Users can add, rename, delete, indent/outdent and reorder bones (drag-and-drop and keyboard).
- **FR-1.2** Each bone gets a section-style number based on its position (`1`, `1.1`, `1.1.2`). Numbers recompute live on any structural change.
- **FR-1.3** Every bone also has a **stable internal ID** that never changes. Numbers are display-only, because reordering changes them. Log entries cross-reference bones by ID with `[[bone:12]]`, rendered as the bone's current number and title, so references don't break on reorder. The entry editor's **Link bone…** button inserts them.
- **FR-1.4** Deleting a bone that has children or log entries asks for confirmation and is undoable.
- **FR-1.5** New projects start with an optional category template (6M: Man/People, Machine, Method, Material, Measurement, Environment; or 4S, 8P, or blank).
- **FR-1.6** Maximum supported depth is 4 levels below the spine. Deeper nesting is allowed in the data, but the diagram collapses it (see FR-2.7).

### 4.1a Categories
- **FR-1a.1** Top-level bones are **categories**, a distinct kind of bone. Every cause belongs to exactly one category.
- **FR-1a.2** The kind is determined by position (top level = category), but moves across that boundary are explicit. Outdenting a cause to the top level, or indenting a category under another bone, asks for confirmation ("Convert to category?" / "Convert to cause?").
- **FR-1a.3** On the diagram, categories are drawn the classic way: a labeled box at the outer end of a main bone, visually distinct from causes. They are numbered `1`, `2`, `3`…, and their causes `1.1`, `1.2`….
- **FR-1a.4** Categories have a title and description. Their level and status are **derived** from their causes, not set directly (FR-3.7, FR-3a.6). Categories have **no evidence log**; evidence lives on causes.
- **FR-1a.5** In the grid, category rows are styled as group headers (bold, tinted background).
- **FR-1a.6** Category templates (FR-1.5) pre-fill the category set for new projects. Categories can be added, renamed, reordered and deleted freely afterwards.

### 4.2 Diagram
- **FR-2.1** The diagram renders automatically from the bone hierarchy and updates within ~100 ms of any edit, with no manual "refresh".
- **FR-2.2** Classic Ishikawa layout: horizontal spine ending in the head (problem statement). Top-level bones alternate above and below the spine at an angle. Sub-bones branch off alternately horizontally and diagonally. Causes read **top-down** on both sides of the spine (1.1 above 1.2), whether the category is above or below it, and branches along a horizontal bone read **left to right** (1.1.1 left of 1.1.2).
- **FR-2.3** Each bone shows its number and title. Long titles wrap to a configurable maximum width.
- **FR-2.4** Bones are colored by Level. Color applies to the bone line and label; Ruled Out bones are additionally dimmed or struck through so they read as eliminated even in grayscale.
- **FR-2.5** Clicking a bone selects it in the data editor and shows its details. Selecting in the editor highlights the bone in the diagram and scrolls it into view.
- **FR-2.6** Zoom (wheel/pinch), pan (drag), "fit to view" and "zoom to selection".
- **FR-2.7** Bones can be collapsed or expanded in the diagram, and collapsed state is saved per project. Collapsed bones show a child-count badge.
- **FR-2.8** Style presets: **Default** and **Compact** spacing at minimum. Preset parameters (bone angle, spacing, font size, label width) live in one style object, so more presets are cheap to add.
- **FR-2.9** The layout must avoid label overlap. It is optimized for the typical investigation of **20–40 bones**, and must remain usable up to a design limit of 8 categories and 150 bones.
- **FR-2.10** Each bone shows its Status as a small, unobtrusive indicator next to its title: a colored dot, or an icon. It is shown by default and can be toggled off (e.g., for clean exports). The legend includes a Status key when indicators are shown.

### 4.3 Levels and legend
- **FR-3.1** Levels are user-configurable per project: name, color, definition text, sort order.
- **FR-3.2** Default levels: Root Cause, Direct Cause, Immediate Cause, **Potential Contributor (default for new bones)**, Ruled Out.
- **FR-3.3** One level is marked as the default for new bones.
- **FR-3.4** Deleting a level in use requires reassigning its bones to another level.
- **FR-3.5** The diagram shows a legend with each level's color, name and definition. The legend can be shown or hidden and positioned (bottom left, bottom right, or right side), and is included in exports. Legend visibility, position and status-dot visibility are saved per project, so exports look the same on every machine.
- **FR-3.6** Level sets can be saved as an app-wide template for new projects.
- **FR-3.7 Level roll-up.** A bone with children takes the level of its **highest-severity child**, applied recursively up to the categories. Severity is the level sort order (FR-3.1), so reordering levels changes the roll-up.
  - Example: a cause whose children are *Potential Contributor* and *Direct Cause* displays as *Direct Cause*. If every child is *Ruled Out*, the parent is *Ruled Out*.
  - Roll-up is **configurable per project**: *On* (default) or *Off*. With *Off*, every bone's level is set manually, as for leaf bones.
  - With roll-up on, a parent cause can be **pinned** to a manual level that overrides the roll-up (e.g., the team judges the parent itself to be the root cause). Pinned bones show a pin marker in the grid, the details panel and the diagram. Unpinning returns the bone to its rolled-up level.
  - Categories cannot be pinned; their level is always rolled up.
  - Rolled-up levels are shown as read-only (with an "auto" hint) in the grid and details, so it's clear why a level can't be edited directly.
  - Rolled-up levels are computed, not stored, so they can never go stale.

### 4.3a Statuses
- **FR-3a.1** Statuses are configurable per project: name, indicator color, optional icon, sort order.
- **FR-3a.2** Default statuses: **Not Started (default for new bones)**, In Progress, Blocked, Under Review, Complete.
- **FR-3a.3** Deleting a status in use requires reassigning its bones, as with levels.
- **FR-3a.4** Status is editable from the grid (dropdown), the details panel, and a right-click menu on the diagram.
- **FR-3a.5** The status bar shows a progress summary (e.g., "12 / 30 complete, 2 blocked").
- **FR-3a.6** A category's status is shown as a summary of its causes rather than a single status value: "5/8 complete" in the grid and details, and a progress ring in the category box on the diagram (hidden along with status dots).
- **FR-3a.7** Statuses of parent causes are **set manually in v1** and do not roll up.
- **FR-3a.8 (Future)** Optional status roll-up rules for parent causes, e.g. *Blocked* if any child is Blocked, *Complete* if all children are Complete, *In Progress* if any child has started. To keep this cheap later, status derivation should live in one function alongside level roll-up, and statuses should carry a role (not-started / active / blocked / review / done) so rules don't depend on user-editable names.

### 4.4 Bone details
- **FR-4.1** Each cause has a title, a description (markdown), a level, a status, and an evidence log. Categories have a title and description only (§4.1a).
- **FR-4.2** **[Proposed]** Optional fields: owner/assignee, tags. Hidden unless enabled, to keep the UI clean.

### 4.5 Evidence log
- **FR-5.1** Each cause has an ordered list of log entries. Each entry is a section of the investigation under that bone, with an optional title, a body (markdown), `created_at`, `updated_at`, and its author and last editor (from the user stamps, FR-6.4).
- **FR-5.1a** Entries display in chronological order by default, which makes the list read as a progress history. Users can also reorder them manually to structure the bone's write-up as sections. A toggle switches between "manual order" and "by date".
- **FR-5.1b** Entries can be collapsed to their title and first line, to keep long bones scannable.
- **FR-5.2** Markdown rendering supports CommonMark + GFM tables, task lists and fenced code blocks **with syntax highlighting**.
- **FR-5.3** Images can be pasted from the clipboard, dragged in, or picked from disk. They are stored in the project file and rendered inline as previews. Clicking a preview opens it full-size.
- **FR-5.4** Entries are freely editable after creation, which updates `updated_at`. No per-entry revision history is required; the general `history` table (FR-6.3) still records that an edit happened.
- **FR-5.5** Edit and preview modes: an edit box with live preview, or toggle between them.
- **FR-5.6** A project-wide "timeline" view listing all log entries across bones in chronological order.
- **FR-5.7** Raw HTML in entries is not rendered (it shows as text), and `javascript:` links are not clickable, so pasted content can't inject markup.

### 4.6 Tracking and timestamps
- **FR-6.1** All entities (project, bone, level, status, log entry, attachment) carry `created_at` and `updated_at`.
- **FR-6.2** Timestamps are stored in UTC (ISO-8601) and displayed in local time.
- **FR-6.3** An append-only `history` table records major actions (create, update, delete, move, level change) with timestamp, entity, and before/after values. This powers the audit trail and can power undo across sessions later.
- **FR-6.4 User identity stamps.** All entities also carry `created_by` and `updated_by`, and each `history` row records who made the change. Log entries show their author and, if different, the last editor.
- **FR-6.5** The current user's display name comes from Settings. On first run it defaults to the Windows account name (`getpass.getuser()`), and can be changed. No login or authentication: the identity is a label, not a security control.
- **FR-6.6** Users are stored in a small `user` table inside the project file, so stamps stay readable when the file is opened on another machine or by another person.

### 4.7 Persistence
- **FR-7.1** The project file **is** a SQLite database (proposed extension `.fishy`). Every change is written to it immediately in a transaction, so there is no save button and no unsaved state.
- **FR-7.2** "Save As…" or "Duplicate" uses the SQLite online backup API to produce a consistent copy.
- **FR-7.3** Images are stored as BLOBs inside the file, so a project is one portable file that can be emailed.
- **FR-7.4** The schema has a version number and forward migrations. Opening an older file migrates it after making a backup copy.
- **FR-7.5** Undo/redo (Ctrl+Z / Ctrl+Y) for all edits within a session. Each undo step is also written through to the database immediately.
- **FR-7.6** Recent-files list. Reopen the last project on launch. *(Done: Open ▾ lists the last 8.)*
- **FR-7.7** File locking: warn if the same project is already open in another instance. *(Done: a hidden `name.fishy.lock` beside the project; warns, with "Open anyway".)*

### 4.8 Export
- **FR-8.1** Export the diagram to **PNG** (configurable DPI/scale), **SVG** (vector) and **PDF** (vector, page size and orientation selectable, fit-to-page).
- **FR-8.2** Exports include the legend and an optional title block (project name, problem statement, date).
- **FR-8.3** Export respects the current style preset and collapse state, with an option to "export fully expanded".
- **FR-8.4** Copy diagram to clipboard as an image.
- **FR-8.5** Export bone data to text (as a bulleted list, tabbed by depth) or the full project to JSON (for interchange and backup).
- *(Done in milestone 6.)* Also: the outline can be copied straight to the clipboard; the JSON includes images as base64 (optional) and computed numbers and rolled-up levels; legend and status-dot defaults come from the project, other export choices are remembered app-wide.

## 5. User Interface

- **UI-1** A clean, modern look from simple theming: light and dark themes, one accent color, rounded surfaces, generous spacing, clear typography. Material Design is a source of inspiration, not a standard to follow. *(Done: System / Light / Dark, following the Windows setting live by default. The diagram canvas and legend stay white in both modes, like paper, to match exports.)*
- **UI-2** Layout: a resizable **split view**, with the diagram on top or left and the data editor on the bottom or right. One click maximizes either pane, which gives tab-like behavior when wanted. Splitter position and orientation are remembered.
- **UI-3** The data editor is a tree-grid (columns: Number, Title, Level, Status, # Log entries, Updated) beside or above a details panel for the selected bone.
- **UI-4** Inline editing in the grid: title text, a level dropdown with color swatches, a status dropdown. Dropdowns list choices by name (never by id) and commit as soon as one is picked.
- **UI-5** The two panes share a single selection model and are always in sync.
- **UI-6** Keyboard-first editing in the tree (agreed map):

  | Key | Action |
  |---|---|
  | Enter | New sibling below, ready to type its title |
  | Tab | New child, ready to type its title |
  | Space / F2 | Rename (edit the title) |
  | ← / → | Outdent / indent |
  | Ctrl+↑ / Ctrl+↓ (also Alt+↑ / Alt+↓) | Move up / down |
  | Shift+Tab | Outdent |
  | Delete | Delete (asks when the bone has sub-causes or evidence) |
  | + / − | Expand / collapse (← / → are used for outdent/indent) |
  | Ctrl+F | Find by title |
  | Ctrl+Z / Ctrl+Y | Undo / redo |
- **UI-7** Filter and search: by text, level and status. Filtered-out bones are dimmed in the diagram. *(Done in milestone 7. Find by title (Ctrl+F) jumps; Filter (Ctrl+Shift+F) filters by text, level and status. OR within a list, AND across lists; levels as rolled up; matches' ancestors stay visible in grey; non-matches are hidden in the grid and faded on the diagram; exports ignore the filter.)*
- **UI-8** Settings dialog for theme, default style preset, level templates and the current user's display name. *(Done; also the default category template.)*

## 6. Non-Functional Requirements

- **NFR-1 Platform:** Windows 10/11 is the only target. Cross-platform support is not a priority, but isn't ruled out either: avoid Windows-specific code where it costs nothing, and macOS/Linux get whatever Python and Qt provide, untested.
- **NFR-2 Performance:** a typical diagram (20–40 bones) re-lays out and repaints with no perceptible delay (< 50 ms); at the 150-bone design limit, < 100 ms. The app starts in < 3 s.
- **NFR-3 Data safety:** no edit is ever lost on crash. Writes are transactional, and the file stays consistent if the app is killed mid-write.
- **NFR-4 Offline:** fully offline; no network access required.
- **NFR-5 Distribution:** a single installer or portable executable; users do not need Python installed. (FUTURE, for v1, direct python is OK)
- **NFR-6 Accessibility:** colors never carry meaning alone (labels and patterns accompany level colors); full keyboard navigation; respects OS scaling (HiDPI).

## 7. Feasibility Assessment

**Overall: highly feasible** for a single developer. Every requirement maps to mature,
well-documented building blocks. The real work is in two places: the **fishbone layout
algorithm** and **polish of the markdown/evidence experience**.

| Area | Difficulty | Notes / risk |
|---|---|---|
| Hierarchical editor (tree-grid) | Low–Med | `QTreeView` + custom `QAbstractItemModel`. Drag-drop reordering in custom models is fiddly but well-trodden. |
| Persistence (SQLite write-through) | Low | Python's stdlib `sqlite3`. Write-through on every change is simple and fast at this data size. |
| Undo/redo | Med | `QUndoStack` with command objects that update the model and the DB. Design it in from day one; retrofitting is painful. |
| **Fishbone auto-layout** | **Med–High** | **No off-the-shelf library does Ishikawa layout well**, so it's a custom recursive layout: measure subtree extents, then place along spine and branches. Variable label lengths and deep nesting cause most of the difficulty. *Biggest technical risk; prototype first.* |
| Diagram rendering and interactivity | Low–Med | `QGraphicsScene`/`QGraphicsView`: hit-testing, zoom/pan, selection and HiDPI come free. |
| Export PNG/SVG/PDF | Low | Render the scene to `QImage`, `QSvgGenerator` and `QPdfWriter`. Vector output is native. |
| Markdown + syntax highlighting | Med | `markdown-it-py` (GFM) → HTML; `Pygments` with inline styles for code. Display in `QTextBrowser` (lightweight, limited CSS) or `QWebEngineView` (full fidelity, adds ~150 MB+). Start with `QTextBrowser`. |
| Image paste/preview | Low–Med | Clipboard → BLOB in DB → served to the renderer via a custom resource handler (`fishy-img://<id>`). |
| Modern look | Low–Med | Simple theming is enough (Material is only inspiration): the Fusion style plus a hand-written QSS theme and a light/dark palette. |
| Packaging | Med | `pyside6-deploy` (Nuitka-based, official) or PyInstaller. Expect some tuning; WebEngine makes bundles much larger. |

**Biggest risks and mitigations**
1. *Layout quality* — Build a standalone layout prototype in milestone 1, before any UI
   polish, and test it against a set of synthetic trees (wide, deep, long labels).
2. *Undo + write-through + sync complexity* — Use one central command layer as the only
   mutation path (UI → Command → Repository → Model signals → both views).
3. *Markdown fidelity in QTextBrowser* — If it proves too limited (e.g., code-block
   backgrounds, tables), switch the log viewer to `QWebEngineView`. Isolate the renderer
   behind an interface so the swap is local.

## 8. Recommended Environment

### 8.1 Language and stack — **Python + PySide6 (Qt Widgets)**
Python/PySide6 can do this job well, and it matches your preference and past experience.

| Concern | Choice |
|---|---|
| Python | **3.15** (already pinned). PySide6 6.12 supports `>=3.10,<3.16` and ships `abi3` wheels for Windows x64/ARM64. If any dependency lags on 3.15, drop the pin to 3.14 with no code changes. |
| GUI | **PySide6 6.12 (LTS)**, Qt Widgets |
| Project/env | **uv** (already used): `uv add pyside6 markdown-it-py pygments` |
| Markdown | `markdown-it-py` (+ `mdit-py-plugins` for task lists etc.) |
| Code highlighting | `Pygments` (`HtmlFormatter(noclasses=True)` for QTextBrowser) |
| Storage | stdlib `sqlite3` with a thin repository layer (no ORM needed at this size) |
| Testing | `pytest`, `pytest-qt`; snapshot tests for the layout engine (geometry in, positions out) |
| Lint/types | `ruff`, `pyright` (or `mypy`) |
| Packaging | `pyside6-deploy` (preferred) or PyInstaller |
| IDE | VS Code (Python + Ruff extensions) or PyCharm; Qt Designer optional |

### 8.2 Qt Widgets vs Qt Quick (QML) — **Decided: Qt Widgets**
Since strict Material Design isn't required, Qt Quick's main advantage (its built-in Material
style) no longer matters. Widgets is best for data-heavy editing (tree-grid, inline editors,
drag-drop), `QGraphicsScene` is ideal for the diagram, and it's what you know from past work.
Theming is the Fusion style plus a QSS file and light/dark `QPalette`s.

### 8.3 Alternative considered — TypeScript (Tauri or Electron + React + MUI)
This stack fits a modern look (MUI), markdown (react-markdown + Shiki) and SVG diagrams
very naturally, and diagram export is easy. Costs: two-language architecture with Tauri
(Rust backend) or a heavier Electron runtime, and a less pleasant tree-grid editing story
without a commercial grid. **Reasonable choice, but not better enough to override your
Python preference.**

## 9. Proposed Architecture

```
fishy/
  app.py                 # entry point, QApplication, theme
  domain/                # pure-Python dataclasses: Project, Bone, Level, LogEntry, Attachment
  storage/
    db.py                # connection, PRAGMAs, migrations
    repository.py        # CRUD; the only code that touches SQL
    schema/              # 001_init.sql, 002_..., etc.
  commands/              # QUndoCommand subclasses: AddBone, MoveBone, SetLevel, AddLogEntry…
  models/
    bone_tree_model.py   # QAbstractItemModel over the bone hierarchy
    selection.py         # shared selection model
  layout/
    fishbone_layout.py   # PURE function: tree + style -> positioned geometry (no Qt widgets)
    styles.py            # Default, Compact presets
  views/
    diagram_view.py      # QGraphicsView/Scene; draws geometry from layout/
    editor_view.py       # tree-grid
    details_panel.py     # fields + log list
    markdown_view.py     # renderer interface (QTextBrowser impl; WebEngine impl later)
  export/                # png/svg/pdf, outline text, json
tests/
```

**Data flow (single direction):** user action → `QUndoCommand` → `repository` (SQL
transaction) → model emits change signals → diagram re-lays out and the editor updates.
Both views read from the same model, which guarantees sync (UI-5).

Keeping `fishbone_layout` a **pure function** with no Qt widget dependencies makes it unit-
testable and lets you iterate on it in isolation.

## 10. Proposed Schema (v1)

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;     -- checkpoint + switch to DELETE on close so the file is self-contained

CREATE TABLE meta      (key TEXT PRIMARY KEY, value TEXT);           -- schema_version, app_version
CREATE TABLE user      (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
                        account_name TEXT,                    -- e.g. Windows login, for matching on reopen
                        created_at TEXT NOT NULL);
-- Every table below also has: created_by / updated_by INTEGER REFERENCES user(id)
CREATE TABLE project   (id INTEGER PRIMARY KEY CHECK (id = 1),
                        name TEXT NOT NULL, problem_statement TEXT NOT NULL DEFAULT '',
                        style_preset TEXT NOT NULL DEFAULT 'default',
                        level_rollup INTEGER NOT NULL DEFAULT 1,   -- FR-3.7 on/off
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE level     (id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
                        definition TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL,
                        is_default INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE status    (id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
                        icon TEXT,                            -- optional icon name; dot drawn if NULL
                        role TEXT,                            -- not_started|active|blocked|review|done; for future roll-up (FR-3a.8)
                        sort_order INTEGER NOT NULL,
                        is_default INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE bone      (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES bone(id) ON DELETE CASCADE,
                        position INTEGER NOT NULL,            -- order among siblings
                        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                        level_id INTEGER REFERENCES level(id),   -- manual level: leaves, pinned parents, or roll-up off;
                                                                  -- NULL for categories (always derived)
                        level_pinned INTEGER NOT NULL DEFAULT 0, -- FR-3.7 override of roll-up
                        status_id INTEGER REFERENCES status(id), -- NULL for categories (summary is derived)
                        collapsed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE log_entry (id INTEGER PRIMARY KEY, bone_id INTEGER NOT NULL REFERENCES bone(id) ON DELETE CASCADE, -- causes only
                        position INTEGER NOT NULL,            -- manual section order
                        title TEXT, body_md TEXT NOT NULL,           -- author = created_by
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE attachment(id INTEGER PRIMARY KEY, log_entry_id INTEGER REFERENCES log_entry(id) ON DELETE SET NULL,
                        filename TEXT, mime TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE history   (id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER REFERENCES user(id),
                        action TEXT NOT NULL,
                        entity TEXT NOT NULL, entity_id INTEGER, before_json TEXT, after_json TEXT);
```

Numbers (`1.2.3`), a bone's kind (category = `parent_id IS NULL`), rolled-up levels and
category status summaries are all **computed**, not stored.

### 10.1 Implementation notes (milestone 2)

The authoritative schema is `fishy/storage/schema/001_init.sql`. It differs from the sketch
above in these deliberate ways:

- **Journal mode:** `journal_mode=DELETE` with `synchronous=FULL` instead of WAL. It is just as
  crash-safe (verified by a test that kills the process mid-transaction), and it never leaves
  `-wal`/`-shm` side files, so a `.fishy` file can be copied or emailed while open.
- **Stable ids:** `bone`, `log_entry` and `attachment` use `AUTOINCREMENT`, so a deleted id is
  never reused. Plain SQLite reuses the highest id after a delete, which would break
  cross-references (FR-1.3) and undo.
- **Constraints in the database:** a `CHECK` enforces that categories have no level or status and
  causes have both. `status.role` is restricted to the five roles. `PRAGMA application_id`
  marks the file as a fishy project, so other SQLite files are rejected on open.
- **`level.eliminated`** flag marks levels drawn dimmed or struck through (Ruled Out), instead of
  matching on the level's name.
- **Attachments** cascade-delete with their log entry (no orphaned blobs). Undo restores them.
- **Users:** `created_by` / `updated_by` on every entity table, plus `history.user_id`.
- **Grid model:** Title is model column 0 (the tree column). The editor shows the number column
  first by reordering header sections.
- **Schema v2** (`002_display_settings.sql`, milestone 4) adds `project.show_legend`,
  `project.legend_position` and `project.show_status`. Files from milestones 2–3 (v1) upgrade
  automatically on open, after a `.bak-v1` backup copy is written next to them.
- **Level templates** (FR-3.6) are stored in the app's settings, not in project files: "Use these
  levels for new projects" in the Levels & statuses dialog.
- **Evidence (milestone 5):** entries are Markdown rendered by `markdown-it-py` (+ `mdit-py-plugins`
  for task lists) with Pygments highlighting, shown in `QTextBrowser`. That proved good enough for
  tables, code and images, so `QWebEngineView` wasn't needed. Images are referenced as
  `![caption](attachment:<id>)`. Images pasted into an editor are stored straight away with no
  owning entry; saving adopts them, cancelling deletes them, and opening a project removes any
  left behind by a crash. Undoing an entry's creation removes its images; redo restores them.
  The manual-vs-date order toggle is an app setting, not a project setting.
- **Theme (UI-1, after milestone 5):** `fishy/theme.py` owns every UI color. It applies an explicit
  Fusion `QPalette` per mode (so nothing half-follows the OS, which caused unreadable white-on-white
  entries in Windows dark mode) plus a small app stylesheet; widgets opt in with properties
  (`muted`, `separator`, `#entryCard`) instead of hardcoding colors. Markdown entries switch code
  backgrounds and the Pygments style (`default` / `github-dark`). The mode is an app setting.
- **Export (milestone 6):** exports are drawn from a separate off-screen scene (`fishy/export/`),
  so they never include the selection, zoom or UI theme. Diagram fonts are in points, which Qt
  scales by the output device's DPI, so SVG and PDF are written at the screen's logical 96 dpi
  (still vector) and the PNG DPI is set as metadata only after painting; otherwise text came out
  tiny, huge or clipped. PDFs are one page, scaled to fit and centered.
- **Filter (milestone 7):** the filter is view-only. The grid hides rows with
  `QTreeView.setRowHidden` instead of a proxy model, so selection, drag-and-drop, keyboard
  editing and undo keep working on the real model. It's recomputed after every edit, and the
  current bone stays visible while you work on it (e.g. one just added with Enter).
- **Open-twice lock (milestone 7):** `QLockFile` beside the project (hidden on Windows),
  stale-checked by process id, so a crash on the same computer never blocks reopening. If
  the folder is read-only, the project opens without a lock.
- **Settings:** the display name, theme, new-project defaults and level template are app
  settings (per computer, in the registry); everything about a project stays in its file.

## 11. Decisions

### 11.1 Decided

| # | Question | Decision |
|---|---|---|
| Q1 | Split panes or tabs? | Resizable split view with one-click maximize for either pane (UI-2). |
| Q2 | Level vs Status? | Separate fields. Level classifies the cause and drives bone color. Status tracks investigation progress (Not Started, In Progress, Blocked, Under Review, Complete) and shows as a small dot or icon beside the title (FR-2.10, §4.3a). |
| Q3 | Parent level vs children's levels? | A parent automatically takes the highest-severity level among its children; configurable per project (on by default) (FR-3.7). |
| Q4 | Are top-level bones special? | Yes. Top-level bones are categories, treated as a distinct kind of bone (§4.1a). |
| Q5 | Log entry editing / history? | Freely editable, with no revision history. Each entry is a section of the investigation under its bone; in date order the entries also read as a progress history (FR-5.1–5.4). |
| Q6 | User identity? | Basic identity stamps (`created_by` / `updated_by`, history author); display name from settings, no authentication (FR-6.4–6.6). |
| Q7 | Typical size? | Usually 20–40 bones. Optimize for that; 150 bones remains the design limit (FR-2.9, NFR-2). |
| Q8 | Platforms? | Windows only for now. Cross-platform isn't ruled out, but gets only what Python/Qt provide (NFR-1). |
| Q9 | Strict Material Design? | No. Simple theming for a modern look; Qt Widgets chosen (UI-1, §8.2). |
| Q10 | Embedded or linked images? | Embedded in the project file (FR-7.3). |
| Q11 | Printable investigation report? | Valuable, but a future focus; out of scope for v1 (§1.2). |
| Q12 | Import from other tools? | No import in v1 (§1.2). |
| Q13 | Manual order for log entries? | Included, with a toggle between manual order and date order (FR-5.1a). |
| Q14 | Do categories have an evidence log? | No. Categories have a title and description only (FR-1a.4). |
| Q15 | Can a parent cause be pinned to a manual level? | Yes, with a visible pin marker; categories can't be pinned (FR-3.7). |
| Q16 | Do parent causes roll up status? | Not in v1; set manually. Rule-based roll-up (Blocked if any child blocked, Complete if all complete, etc.) is planned for the future (FR-3a.7, FR-3a.8). |

### 11.2 Open

No open questions. Remaining **[Proposed]** items are minor and can be settled during implementation.

## 12. Milestones

1. ✅ **Layout spike** — Pure-Python layout engine plus a bare `QGraphicsView` driven by a hard-coded tree. Validate the Default and Compact presets against stress trees.
2. ✅ **Data core** — Schema, migrations, repository, tree model, undo commands, write-through, user stamps. Tests.
3. ✅ **Editor + sync** — Tree-grid, details panel, shared selection, click-to-navigate both ways.
4. ✅ **Levels, statuses & legend** — Level and status configuration UI, level roll-up and pinning, category status summaries, colors, status indicators, legend on the diagram.
5. ✅ **Evidence log** — Markdown edit/preview, syntax highlighting, image paste/preview.
6. ✅ **Export** — PNG/SVG/PDF with title block and legend; clipboard; outline text and JSON.
7. ✅ **Polish** — Light/dark theme (done early, after M5: System/Light/Dark, follows Windows live; diagram canvas stays white), filter by text/level/status (UI-7), Settings (UI-8), recent files (FR-7.6), open-twice warning (FR-7.7). (Packaging/installer is future work, NFR-5; v1 runs directly from Python.)
