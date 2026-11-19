-- fishy project file, schema version 1.
-- Applied inside a transaction by storage.db.migrate(); PRAGMA user_version is
-- set by the migration runner, not here.
--
-- bone, log_entry and attachment use AUTOINCREMENT so ids are never reused
-- after a delete: ids are stable references (FR-1.3) and undo restores them.

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE user (
    id           INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    account_name TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE project (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    name              TEXT NOT NULL,
    problem_statement TEXT NOT NULL DEFAULT '',
    style_preset      TEXT NOT NULL DEFAULT 'Default',
    level_rollup      INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    created_by        INTEGER REFERENCES user(id),
    updated_by        INTEGER REFERENCES user(id)
);

CREATE TABLE level (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    definition  TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL,          -- 0 = most severe
    is_default  INTEGER NOT NULL DEFAULT 0,
    eliminated  INTEGER NOT NULL DEFAULT 0, -- drawn dimmed (e.g. Ruled Out)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    updated_by  INTEGER REFERENCES user(id)
);

CREATE TABLE status (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'not_started'
                CHECK (role IN ('not_started', 'active', 'blocked', 'review', 'done')),
    icon        TEXT,
    sort_order  INTEGER NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    updated_by  INTEGER REFERENCES user(id)
);

CREATE TABLE bone (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES bone(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,          -- order among siblings, 0-based, contiguous
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    level_id     INTEGER REFERENCES level(id),   -- NULL for categories
    level_pinned INTEGER NOT NULL DEFAULT 0,
    status_id    INTEGER REFERENCES status(id),  -- NULL for categories
    collapsed    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    created_by   INTEGER REFERENCES user(id),
    updated_by   INTEGER REFERENCES user(id),
    -- categories (top level) carry no level/status; causes must have both
    CHECK ((parent_id IS NULL AND level_id IS NULL AND status_id IS NULL)
        OR (parent_id IS NOT NULL AND level_id IS NOT NULL AND status_id IS NOT NULL))
);
CREATE INDEX bone_parent ON bone(parent_id, position);

CREATE TABLE log_entry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bone_id     INTEGER NOT NULL REFERENCES bone(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,           -- manual section order
    title       TEXT,
    body_md     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES user(id),
    updated_by  INTEGER REFERENCES user(id)
);
CREATE INDEX log_entry_bone ON log_entry(bone_id, position);

CREATE TABLE attachment (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    log_entry_id  INTEGER REFERENCES log_entry(id) ON DELETE CASCADE,
    filename      TEXT,
    mime          TEXT NOT NULL,
    data          BLOB NOT NULL,
    sha256        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    created_by    INTEGER REFERENCES user(id),
    updated_by    INTEGER REFERENCES user(id)
);
CREATE INDEX attachment_entry ON attachment(log_entry_id);

CREATE TABLE history (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    user_id     INTEGER REFERENCES user(id),
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   INTEGER,
    before_json TEXT,
    after_json  TEXT
);
