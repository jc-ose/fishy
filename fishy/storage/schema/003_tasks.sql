-- Schema v3: tasks (spec docs/superpowers/specs/2026-11-19-tasks-design.md §4.1).
--
-- A task has a stable id (AUTOINCREMENT, never reused, so [[task:N]] references
-- don't break), configurable states, and links to zero or more bones. Deleting a
-- bone removes only its links; the task stays.

CREATE TABLE task_state (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    symbol      TEXT NOT NULL,             -- one glyph drawn as the checkbox
    role        TEXT NOT NULL DEFAULT 'not_started'
                CHECK (role IN ('not_started', 'active', 'blocked', 'review', 'done')),
    sort_order  INTEGER NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    updated_by  INTEGER REFERENCES user(id)
);

CREATE TABLE task (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',  -- plain text
    state_id    INTEGER NOT NULL REFERENCES task_state(id),
    assignee    TEXT,                      -- free text, NULL = unassigned
    due_date    TEXT,                      -- ISO date 'YYYY-MM-DD', NULL = none
    critical    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    updated_by  INTEGER REFERENCES user(id)
);

CREATE TABLE task_bone (
    task_id     INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    bone_id     INTEGER NOT NULL REFERENCES bone(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    PRIMARY KEY (task_id, bone_id)
);
CREATE INDEX task_bone_bone ON task_bone(bone_id);

INSERT INTO task_state (name, color, symbol, role, sort_order, is_default, created_at, updated_at)
VALUES
    ('To do',       '#90a4ae', '☐', 'not_started', 0, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('In progress', '#1e88e5', '◐', 'active',      1, 0,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('Blocked',     '#e53935', '⊘', 'blocked',     2, 0,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('Done',        '#43a047', '☑', 'done',        3, 0,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
