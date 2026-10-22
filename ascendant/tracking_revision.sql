-- Version 4: transactional invalidation, including edits from other SQLite clients.
CREATE TABLE tracking_revision (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    identity TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT INTO tracking_revision(id, identity) VALUES (1, lower(hex(randomblob(16))));
CREATE TRIGGER revision_videos_insert AFTER INSERT ON videos
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_videos_update AFTER UPDATE ON videos
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_videos_delete AFTER DELETE ON videos
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_trackers_insert AFTER INSERT ON trackers
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_trackers_update AFTER UPDATE ON trackers
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_trackers_delete AFTER DELETE ON trackers
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_snapshots_insert AFTER INSERT ON snapshots
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_snapshots_update AFTER UPDATE ON snapshots
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_snapshots_delete AFTER DELETE ON snapshots
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_tracking_samples_insert AFTER INSERT ON tracking_samples
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_tracking_samples_update AFTER UPDATE ON tracking_samples
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_tracking_samples_delete AFTER DELETE ON tracking_samples
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_collection_errors_insert AFTER INSERT ON collection_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_collection_errors_update AFTER UPDATE ON collection_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_collection_errors_delete AFTER DELETE ON collection_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watches_insert AFTER INSERT ON creator_watches
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watches_update AFTER UPDATE ON creator_watches
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watches_delete AFTER DELETE ON creator_watches
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_seen_insert AFTER INSERT ON creator_seen
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_seen_update AFTER UPDATE ON creator_seen
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_seen_delete AFTER DELETE ON creator_seen
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watch_errors_insert AFTER INSERT ON creator_watch_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watch_errors_update AFTER UPDATE ON creator_watch_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER revision_creator_watch_errors_delete AFTER DELETE ON creator_watch_errors
BEGIN
    UPDATE tracking_revision SET revision = revision + 1 WHERE id = 1;
END;
