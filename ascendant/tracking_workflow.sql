-- Version 3: tracking membership is independent of dataset snapshot batches.
CREATE TABLE tracking_samples (
    snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id)
);
INSERT INTO tracking_samples(snapshot_id)
SELECT s.id FROM snapshots s
WHERE s.source = 'scheduled' OR s.tracker_id IS NOT NULL OR NOT EXISTS (
    SELECT 1 FROM snapshot_batch_items i WHERE i.snapshot_id = s.id
);
CREATE VIEW tracking_observations AS
SELECT s.* FROM snapshots s JOIN tracking_samples t ON t.snapshot_id = s.id;
CREATE VIEW latest_tracking_observations AS
SELECT * FROM (
    SELECT t.*, ROW_NUMBER() OVER (PARTITION BY bvid ORDER BY collected_at DESC, id DESC) AS position
    FROM tracking_observations t
) WHERE position = 1;
CREATE VIEW tracking_history AS
SELECT history.*,
       views - previous_views AS views_change,
       CASE WHEN julianday(collected_at) > julianday(previous_collected_at)
            THEN (views - previous_views) / ((julianday(collected_at) - julianday(previous_collected_at)) * 24.0)
       END AS views_per_hour,
       CASE WHEN views > 0 THEN likes * 1.0 / views END AS like_ratio
FROM (
    SELECT t.*, LAG(views) OVER video_time AS previous_views,
           LAG(collected_at) OVER video_time AS previous_collected_at
    FROM tracking_observations t
    WINDOW video_time AS (PARTITION BY bvid ORDER BY collected_at, id)
) AS history;

CREATE TABLE creator_watches (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    interval_seconds INTEGER NOT NULL CHECK (interval_seconds BETWEEN 60 AND 604800),
    video_interval_seconds INTEGER NOT NULL CHECK (video_interval_seconds BETWEEN 60 AND 604800),
    created_at TEXT NOT NULL,
    baseline_at TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    next_check_at TEXT
);
CREATE TABLE creator_seen (
    watch_id INTEGER NOT NULL REFERENCES creator_watches(id),
    bvid TEXT NOT NULL,
    title TEXT,
    published_at INTEGER NOT NULL,
    discovered_at TEXT NOT NULL,
    is_baseline INTEGER NOT NULL CHECK (is_baseline IN (0, 1)),
    tracker_id INTEGER REFERENCES trackers(id),
    PRIMARY KEY(watch_id, bvid)
);
CREATE TABLE creator_watch_errors (
    id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL REFERENCES creator_watches(id),
    attempted_at TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX creator_watches_due ON creator_watches(status, next_check_at);
