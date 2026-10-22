-- Version 1. All *_at timestamps are ISO 8601 UTC; published_at is Unix seconds.
CREATE TABLE videos (
    bvid TEXT PRIMARY KEY,
    aid INTEGER,
    title TEXT,
    creator_uid TEXT,
    creator_name TEXT,
    published_at INTEGER,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE trackers (
    id INTEGER PRIMARY KEY,
    bvid TEXT NOT NULL UNIQUE REFERENCES videos(bvid),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    interval_seconds INTEGER NOT NULL CHECK (interval_seconds BETWEEN 60 AND 604800),
    created_at TEXT NOT NULL,
    next_check_at TEXT,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    CHECK (status = 'paused' OR next_check_at IS NOT NULL)
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    bvid TEXT NOT NULL REFERENCES videos(bvid),
    tracker_id INTEGER REFERENCES trackers(id) ON DELETE SET NULL,
    collected_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('manual', 'scheduled')),
    title TEXT,
    creator_uid TEXT,
    creator_name TEXT,
    published_at INTEGER,
    views INTEGER CHECK (views >= 0),
    likes INTEGER CHECK (likes >= 0),
    coins INTEGER CHECK (coins >= 0),
    favorites INTEGER CHECK (favorites >= 0),
    replies INTEGER CHECK (replies >= 0),
    shares INTEGER CHECK (shares >= 0),
    danmaku INTEGER CHECK (danmaku >= 0),
    UNIQUE (bvid, collected_at),
    CHECK (views IS NOT NULL OR likes IS NOT NULL OR coins IS NOT NULL OR
           favorites IS NOT NULL OR replies IS NOT NULL OR shares IS NOT NULL OR danmaku IS NOT NULL)
);

CREATE TABLE collection_errors (
    id INTEGER PRIMARY KEY,
    bvid TEXT NOT NULL REFERENCES videos(bvid),
    tracker_id INTEGER REFERENCES trackers(id) ON DELETE SET NULL,
    attempted_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('manual', 'scheduled')),
    message TEXT NOT NULL
);

CREATE INDEX snapshots_video_time ON snapshots(bvid, collected_at, id);
CREATE INDEX trackers_due ON trackers(status, next_check_at);
CREATE INDEX errors_video_time ON collection_errors(bvid, attempted_at);

-- Convenient read-only views for DB Browser and CSV exports.
CREATE VIEW latest_snapshots AS
SELECT * FROM (
    SELECT snapshots.*, ROW_NUMBER() OVER (
        PARTITION BY bvid ORDER BY collected_at DESC, id DESC
    ) AS position FROM snapshots
) WHERE position = 1;

CREATE VIEW snapshot_history AS
SELECT history.*,
       views - previous_views AS views_change,
       CASE WHEN julianday(collected_at) > julianday(previous_collected_at)
            THEN (views - previous_views) / ((julianday(collected_at) - julianday(previous_collected_at)) * 24.0)
       END AS views_per_hour,
       CASE WHEN views > 0 THEN likes * 1.0 / views END AS like_ratio
FROM (
    SELECT snapshots.*,
           LAG(views) OVER video_time AS previous_views,
           LAG(collected_at) OVER video_time AS previous_collected_at
    FROM snapshots
    WINDOW video_time AS (PARTITION BY bvid ORDER BY collected_at, id)
) AS history;
