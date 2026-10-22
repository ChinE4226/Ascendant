-- Version 2: explicit whole-collection snapshots reuse immutable observations.
CREATE TABLE snapshot_batches (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('creator', 'weekly', 'random')),
    capture_mode TEXT NOT NULL CHECK (capture_mode IN ('loaded', 'fresh')),
    collection_id TEXT,
    started_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    video_count INTEGER NOT NULL CHECK (video_count > 0),
    scope_json TEXT NOT NULL,
    collection_json TEXT NOT NULL
);

CREATE TABLE snapshot_batch_items (
    batch_id INTEGER NOT NULL REFERENCES snapshot_batches(id),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    position INTEGER NOT NULL,
    PRIMARY KEY (batch_id, position),
    UNIQUE (batch_id, snapshot_id)
);

CREATE INDEX batch_items_snapshot ON snapshot_batch_items(snapshot_id);

CREATE VIEW collection_snapshot_rows AS
SELECT b.id AS batch_id, b.label, b.source_kind, b.capture_mode, b.saved_at,
       b.started_at, b.scope_json, b.collection_json, i.position, s.*
FROM snapshot_batches b
JOIN snapshot_batch_items i ON i.batch_id = b.id
JOIN snapshots s ON s.id = i.snapshot_id;
