BEGIN IMMEDIATE;
ALTER TABLE reviews ADD COLUMN reviewed_at TEXT;
UPDATE reviews SET reviewed_at = updated_at;
ALTER TABLE search_runs ADD COLUMN profile_snapshot_json TEXT CHECK (profile_snapshot_json IS NULL OR json_valid(profile_snapshot_json));
CREATE TABLE profiles (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE job_comments (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX comments_job ON job_comments(job_id, created_at);
PRAGMA user_version = 2;
COMMIT;
