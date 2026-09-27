"""SQLite schema and connection factory.

The schema is (re)applied on every connection via ``CREATE TABLE IF NOT
EXISTS``, so a fresh clone needs no migration step.

It is deliberately lean: it holds what the four steps actually write today.
Columns for features that are not implemented - CV scoring, culture scoring,
interview extraction, salary estimation - were removed rather than left NULL
forever. Add a column back together with the code that writes it.

Because ``CREATE TABLE IF NOT EXISTS`` cannot drop columns, a database created
before that change still carries the old, now-unused columns. Nothing reads
them, so this is harmless, but a recreated database will not have them.

Query helpers live in ``repositories.py``; this module only owns the storage
layer itself.
"""

import sqlite3
from pathlib import Path

from job_seeker_ai import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,   -- internal uuid, NOT any site's native id
    title       TEXT,
    company     TEXT,
    location    TEXT,
    posted      TEXT,               -- the posting's own age string, e.g. "3 days ago"
    description TEXT,
    salary      TEXT,               -- as found on the offer page, number/range only, blank if absent
    url         TEXT,               -- first-seen url (convenience; full history in job_sources)
    status      TEXT DEFAULT 'active',   -- 'active' | 'invalidated' | 'rejected'
    extra_fields TEXT,              -- JSON blob for fields not worth a column yet (unused so far)
    norm_key    TEXT,               -- exact-match dedupe key, see parsers.normalize_key()
    scraped_at  TEXT DEFAULT (datetime('now')),
    -- filled by the enrichment step, not by a scraper:
    deal_breakers_result  BOOLEAN,  -- deal-breakers vs criteria/deal_breakers.md
    deal_breakers_reason  TEXT,
    enriched_at    TEXT,
    filtered_at    TEXT,            -- set by the word filter once must/forbidden words are checked
    filter_reason  TEXT             -- why the word filter rejected it, else NULL
);

CREATE TABLE IF NOT EXISTS job_sources (
    job_id        TEXT NOT NULL REFERENCES jobs(job_id),
    site          TEXT NOT NULL,
    source_id     TEXT NOT NULL,   -- the site's own id for this posting
    url           TEXT NOT NULL,
    search_name   TEXT,            -- which searches.json entry found it
    first_seen_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (site, source_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_norm_key ON jobs(norm_key);
CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs(scraped_at);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with the pipeline's pragmas and ensure the schema.

    WAL plus a busy timeout is what makes the concurrent page workers able
    to share one database file at all.
    """
    con = sqlite3.connect(path or config.DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con
