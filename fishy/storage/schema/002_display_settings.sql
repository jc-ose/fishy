-- Schema v2: per-project diagram display settings (FR-2.10, FR-3.5).
-- Stored in the project so exports and every machine show the diagram the same way.

ALTER TABLE project ADD COLUMN show_legend INTEGER NOT NULL DEFAULT 1;
ALTER TABLE project ADD COLUMN legend_position TEXT NOT NULL DEFAULT 'bottom-left'
    CHECK (legend_position IN ('bottom-left', 'bottom-right', 'right'));
ALTER TABLE project ADD COLUMN show_status INTEGER NOT NULL DEFAULT 1;
