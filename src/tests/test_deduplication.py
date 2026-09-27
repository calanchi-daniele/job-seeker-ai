"""Deduplication rules at insert time.

The dedupe key is ``(site, source_id)``. These lock in that behaviour,
including the deliberate decision *not* to merge across searches.
"""

from job_seeker_ai import repositories
from job_seeker_ai.entities import ScrapedJob

SITE = "linkedin"


def _scraped(source_id, url, title="Backend Engineer"):
    return ScrapedJob(
        source_id=source_id,
        url=url,
        title=title,
        company="Acme",
        location="Remote",
        posted="1d",
        description="desc",
        salary=None,
    )


def test_same_posting_skipped_but_other_searches_are_not(con):
    a = _scraped("1", "https://x/1")
    job_id = repositories.upsert_job(con, a, "search-a", SITE)
    assert job_id

    # exact same posting re-scraped (e.g. a re-run) -> no-op, not a new job
    assert repositories.upsert_job(con, a, "search-a", SITE) is None

    # same title+company+location found via a *different* search -> there is
    # no cross-search dedup, so this is deliberately a brand new row
    job_id2 = repositories.upsert_job(con, _scraped("2", "https://x/2"), "search-b", SITE)
    assert job_id2 and job_id2 != job_id
    assert con.execute("SELECT count(*) FROM jobs WHERE url IN ('https://x/1','https://x/2')").fetchone()[0] == 2

    # genuinely different job -> new canonical row
    job_id3 = repositories.upsert_job(con, _scraped("3", "https://x/3", "Frontend Engineer"), "search-a", SITE)
    assert job_id3 and job_id3 not in (job_id, job_id2)


def test_inserted_row_keeps_every_column(con):
    """Regression for the jobs INSERT that used to raise
    ``sqlite3.OperationalError: 10 values for 9 columns``: nothing was ever
    persisted. Assert the whole row lands, not merely that an id came back.
    """
    scraped = ScrapedJob(
        source_id="99",
        url="https://x/99",
        title="Platform Engineer",
        company="Acme",
        location="Remote",
        posted="2 days ago",
        description="We use C# and .NET.",
        salary="€70,000",
    )
    job_id = repositories.upsert_job(con, scraped, "search-a", SITE)
    con.commit()

    job = repositories.get_job(con, job_id)
    assert job["title"] == "Platform Engineer"
    assert job["company"] == "Acme"
    assert job["location"] == "Remote"
    assert job["posted"] == "2 days ago"
    assert job["description"] == "We use C# and .NET."
    assert job["salary"] == "€70,000"
    assert job["url"] == "https://x/99"
    assert job["norm_key"]
    # the source row is written too, which is what makes dedupe work
    assert repositories.get_job_sources(con, job_id) == [("linkedin", "https://x/99")]


def test_known_posting_is_skipped_before_any_insert(con):
    """The dedupe guard reads job_sources, so a posting already recorded under
    this site+source_id is skipped without touching the jobs table."""
    con.execute("INSERT INTO jobs (job_id,title,company) VALUES ('dup-ex','T','C')")
    con.execute(
        "INSERT INTO job_sources (job_id,site,source_id,url) VALUES ('dup-ex',?,'42','https://x/42')",
        (SITE,),
    )
    con.commit()

    assert repositories.job_source_exists(con, SITE, "42")
    assert repositories.upsert_job(con, _scraped("42", "https://x/42"), "search-a", SITE) is None
