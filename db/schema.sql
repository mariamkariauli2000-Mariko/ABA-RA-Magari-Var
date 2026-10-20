PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'discover',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    last_successful_at TEXT
);

CREATE TABLE IF NOT EXISTS search_runs (
    id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    profile_snapshot_json TEXT CHECK (profile_snapshot_json IS NULL OR json_valid(profile_snapshot_json)),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_sources (
    run_id TEXT NOT NULL REFERENCES search_runs(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'partial', 'blocked', 'failed', 'skipped')),
    attempts_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(attempts_json)),
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location_text TEXT,
    eligible_countries_json TEXT CHECK (eligible_countries_json IS NULL OR json_valid(eligible_countries_json)),
    timezone_requirements TEXT,
    remote_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (remote_status IN ('remote', 'hybrid', 'onsite', 'unknown')),
    employment_type TEXT,
    seniority TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_period TEXT,
    salary_text TEXT,
    skills_json TEXT CHECK (skills_json IS NULL OR json_valid(skills_json)),
    published_at TEXT,
    employer_job_id TEXT,
    availability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (availability IN ('open', 'closed', 'unknown')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max)
);

CREATE TABLE IF NOT EXISTS sightings (
    run_id TEXT NOT NULL REFERENCES search_runs(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    source_url TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    source_job_id TEXT,
    retrieved_at TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
    PRIMARY KEY (run_id, source_id, source_url)
);

CREATE TABLE IF NOT EXISTS reviews (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'shortlisted', 'applied', 'interviewing', 'offer', 'rejected', 'archived')),
    notes TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags_json)),
    updated_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_comments (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_job ON job_comments(job_id, created_at);

CREATE INDEX IF NOT EXISTS jobs_last_seen ON jobs(last_seen_at);
CREATE INDEX IF NOT EXISTS jobs_remote ON jobs(remote_status);
CREATE INDEX IF NOT EXISTS sightings_job ON sightings(job_id);
CREATE INDEX IF NOT EXISTS sightings_source_url ON sightings(source_id, source_url);
PRAGMA user_version = 2;
