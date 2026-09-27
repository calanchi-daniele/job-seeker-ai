"""Every SQL statement the project uses.

The tier above (services, web routes, scrapers) never writes SQL; it calls
a function here. That keeps the query surface in one reviewable place and
means a route change can't silently alter a scrape-time guarantee.

Each function takes an already-open connection so callers control
transactions and lifetimes (``storages.connect()`` opens, the caller
closes). Functions that commit mirror the commit boundaries of the
pre-refactor call sites, so batching behaviour is unchanged.
"""

import uuid

from job_seeker_ai.entities import ScrapedJob
from job_seeker_ai.parsers import normalize_key

# --- meta ----------------------------------------------------------------


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


# --- jobs / job_sources (scrape time) ------------------------------------


def job_source_exists(con, site, source_id):
    return (
        con.execute("SELECT 1 FROM job_sources WHERE site=? AND source_id=?", (site, source_id)).fetchone() is not None
    )


def known_source_ids(con, site):
    """Every site-native id already recorded for ``site``.

    Feeds the in-memory pre-scrape filter: fetching and parsing a job page is
    the expensive part, so an id already in the database should never reach the
    browser. Deliberately a *filter* and not a lock - the authoritative dedupe
    is still the check inside ``upsert_job``, which is what keeps a second
    process from double-inserting.
    """
    return {row[0] for row in con.execute("SELECT source_id FROM job_sources WHERE site = ?", (site,))}


def upsert_job(con, scraped: ScrapedJob, search_name, site) -> str | None:
    """Insert one scraped posting. Returns the new job_id, or ``None`` when
    this exact posting (site + the site's own id) has been seen before.

    The dedupe key is ``(site, source_id)`` - the same posting found again is
    a no-op, but the *same job* advertised under a different site id becomes a
    separate row on purpose. ``norm_key`` is recorded for a future fuzzy pass
    and is deliberately not used to merge.
    """
    if job_source_exists(con, site, scraped.source_id):
        return None  # exact same posting already scraped, nothing to do

    job_id = uuid.uuid4().hex
    key = normalize_key(scraped.title, scraped.company, scraped.location)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,location,posted,description,salary,url,norm_key) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            scraped.title,
            scraped.company,
            scraped.location,
            scraped.posted,
            scraped.description,
            scraped.salary,
            scraped.url,
            key,
        ),
    )
    con.execute(
        "INSERT OR IGNORE INTO job_sources (job_id,site,source_id,url,search_name) VALUES (?,?,?,?,?)",
        (job_id, site, scraped.source_id, scraped.url, search_name),
    )
    con.commit()
    return job_id


# --- jobs (enrich time) --------------------------------------------------


def get_pending_jobs(con):
    """Active jobs the enrichment step has not looked at yet."""
    rows = con.execute(
        "SELECT job_id,title,company,location,posted,salary,description "
        "FROM jobs WHERE enriched_at IS NULL AND status = 'active' ORDER BY scraped_at"
    ).fetchall()
    cols = "job_id title company location posted salary description".split()
    jobs = [dict(zip(cols, r, strict=False)) for r in rows]
    for j in jobs:
        srcs = con.execute("SELECT site, url FROM job_sources WHERE job_id=?", (j["job_id"],)).fetchall()
        j["sources"] = "; ".join(f"{site}: {url}" for site, url in srcs) or "(none recorded)"
    return jobs


def save_job_data(con, data, fields):
    """COALESCE-update ``fields`` and stamp ``enriched_at``. False if no row."""
    n = con.execute(
        "UPDATE jobs SET "
        + ",".join(f"{f}=COALESCE(?,{f})" for f in fields)
        + ", enriched_at=datetime('now') WHERE job_id=?",
        data,
    ).rowcount
    con.commit()
    return n > 0


def invalidate_failing_deal_breakers(con) -> int:
    """Take every active job that failed a deal breaker out of the running.

    Returns the number of rows changed. ``deal_breakers_result`` is 1 for a
    failure and 0 for a pass (see ``services/enrichment.py``), and only
    ``active`` rows move - a job the word filter already ``rejected`` keeps that
    status, since it never got as far as a deal-breaker verdict.

    This is a bulk reconciliation against the whole table, not just the jobs the
    current pass looked at, so it also heals a verdict that was written but
    never acted on (a crash between the two). The consequence is that it has no
    memory of a manual ``reactivate`` in the review site: a job that still fails
    a deal breaker is invalidated again on the next pass.
    """
    cur = con.execute("UPDATE jobs SET status='invalidated' WHERE deal_breakers_result = 1 AND status = 'active'")
    con.commit()
    return cur.rowcount


# --- jobs (filter time) --------------------------------------------------


def list_unfiltered_active_jobs(con):
    """``(job_id, title, description)`` for active jobs the word filter has
    not looked at yet."""
    return con.execute(
        "SELECT job_id, title, description FROM jobs WHERE status='active' AND filtered_at IS NULL"
    ).fetchall()


def reject_job(con, job_id, reason):
    """Mark a job rejected by the word filter. Caller commits."""
    con.execute(
        "UPDATE jobs SET status='rejected', filtered_at=datetime('now'), filter_reason=? WHERE job_id=?",
        (reason, job_id),
    )


def mark_job_filtered(con, job_id):
    """Record that the word filter looked at this job and kept it."""
    con.execute("UPDATE jobs SET filtered_at=datetime('now') WHERE job_id=?", (job_id,))


# --- jobs / job_sources (review site) ------------------------------------


def list_jobs(con, status=None):
    """Every job, newest first, optionally filtered by status.

    There is only one ordering, so no user-supplied fragment reaches the SQL -
    the review site used to take a ``sort`` query parameter and validate it
    against an allowlist, which went away with the score columns it offered to
    sort by.
    """
    sql = "SELECT * FROM jobs"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY scraped_at DESC"

    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def get_job(con, job_id):
    """One job as a dict, or ``None`` when it does not exist."""
    cur = con.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row, strict=False))


def get_job_sources(con, job_id):
    """``(site, url)`` pairs for one job, newest history first."""
    return con.execute("SELECT site, url FROM job_sources WHERE job_id = ?", (job_id,)).fetchall()


def list_sources_grouped(con):
    """``{job_id: [(site, url), ...]}`` for the whole table, one query."""
    sources = {}
    for job_id, site, url in con.execute("SELECT job_id, site, url FROM job_sources"):
        sources.setdefault(job_id, []).append((site, url))
    return sources


def set_job_status(con, job_id, status):
    con.execute("UPDATE jobs SET status = ? WHERE job_id = ?", (status, job_id))
    con.commit()
