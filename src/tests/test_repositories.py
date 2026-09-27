"""Storage layer: schema guarantees, meta round-trip, review-site queries."""

from job_seeker_ai import repositories
from job_seeker_ai.entities import ScrapedJob

# Scoring columns the schema used to carry but nothing ever wrote. None of them
# may come back without the code that populates them.
REMOVED_COLUMNS = {
    "cv_score",
    "cv_reason",
    "params_score",
    "params_reason",
    "interview",
    "company_news",
    "salary_estimate",
}


def test_schema_has_expected_columns(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    assert {
        "job_id",
        "title",
        "company",
        "location",
        "posted",
        "description",
        "salary",
        "url",
        "status",
        "extra_fields",
        "norm_key",
        "scraped_at",
        "deal_breakers_result",
        "deal_breakers_reason",
        "enriched_at",
        "filtered_at",
        "filter_reason",
    } <= cols, cols


def test_schema_has_no_dead_scoring_columns(con):
    """A lean schema on purpose: nothing may sit in the table unwritten."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    assert not (REMOVED_COLUMNS & cols), REMOVED_COLUMNS & cols


def test_schema_has_expected_tables(con):
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "job_sources", "meta"} <= tables


def test_norm_key_is_indexed(con):
    indexes = {r[1] for r in con.execute("PRAGMA index_list(jobs)")}
    assert "idx_jobs_norm_key" in indexes


def test_scraped_at_is_indexed(con):
    """The review list is always ordered by scraped_at, so it needs an index."""
    indexes = {r[1] for r in con.execute("PRAGMA index_list(jobs)")}
    assert "idx_jobs_scraped_at" in indexes


def test_meta_round_trip(con):
    assert repositories.get_meta(con, "absent-key") is None
    assert repositories.get_meta(con, "absent-key", "fallback") == "fallback"

    repositories.set_meta(con, "repo-test", "first")
    assert repositories.get_meta(con, "repo-test") == "first"

    # upsert, not duplicate-insert
    repositories.set_meta(con, "repo-test", "second")
    assert repositories.get_meta(con, "repo-test") == "second"


def test_get_job_returns_none_when_absent(con):
    assert repositories.get_job(con, "nope") is None
    assert repositories.get_job_sources(con, "nope") == []


def test_get_job_returns_every_column_as_a_dict(con):
    con.execute("INSERT INTO jobs (job_id,title,company,location) VALUES ('repo-one','T','C','L')")
    con.commit()

    job = repositories.get_job(con, "repo-one")
    assert job["job_id"] == "repo-one"
    assert job["title"] == "T"
    # a column the enrichment step has not filled yet is still present, as None
    assert "deal_breakers_result" in job and job["deal_breakers_result"] is None


def test_list_jobs_filters_by_status(con):
    con.executemany(
        "INSERT INTO jobs (job_id,title,company,status) VALUES (?,?,?,?)",
        [
            ("repo-a", "A", "C", "active"),
            ("repo-inv", "Inval", "C", "invalidated"),
        ],
    )
    con.commit()

    ids = {j["job_id"] for j in repositories.list_jobs(con, status="invalidated")}
    assert "repo-inv" in ids and "repo-a" not in ids

    ids = {j["job_id"] for j in repositories.list_jobs(con)}
    assert {"repo-a", "repo-inv"} <= ids


def test_list_jobs_is_newest_first(con):
    """There is only one ordering now, and it is by when the job was added."""
    con.executemany(
        "INSERT INTO jobs (job_id,title,company,scraped_at) VALUES (?,?,?,?)",
        [
            ("repo-old", "Old", "C", "2024-01-01 00:00:00"),
            ("repo-new", "New", "C", "2026-01-01 00:00:00"),
        ],
    )
    con.commit()

    order = [j["job_id"] for j in repositories.list_jobs(con)]
    assert order.index("repo-new") < order.index("repo-old")


def test_known_source_ids_is_scoped_to_the_site(con):
    con.executemany(
        "INSERT INTO job_sources (job_id,site,source_id,url) VALUES (?,?,?,?)",
        [
            ("repo-k1", "linkedin", "100", "https://x/100"),
            ("repo-k2", "linkedin", "200", "https://x/200"),
            ("repo-k3", "otherend", "300", "https://x/300"),
        ],
    )
    con.commit()

    ids = repositories.known_source_ids(con, "linkedin")
    assert {"100", "200"} <= ids
    assert "300" not in ids


def test_known_source_ids_reflects_inserts(con):
    """The pre-scrape filter has to stay in step with what is on record."""
    before = repositories.known_source_ids(con, "linkedin")

    repositories.upsert_job(
        con,
        ScrapedJob(
            source_id="fresh-1",
            url="https://x/fresh-1",
            title="T",
            company="C",
            location="L",
            posted="1d",
            description="d",
            salary=None,
        ),
        "search-a",
        "linkedin",
    )

    assert "fresh-1" in repositories.known_source_ids(con, "linkedin") - before


def test_set_job_status(con):
    con.execute("INSERT INTO jobs (job_id,title,company,status) VALUES ('repo-st','T','C','active')")
    con.commit()

    repositories.set_job_status(con, "repo-st", "invalidated")
    assert repositories.get_job(con, "repo-st")["status"] == "invalidated"

    repositories.set_job_status(con, "repo-st", "active")
    assert repositories.get_job(con, "repo-st")["status"] == "active"


def _empty(con):
    """The whole-table reconciliation can only be counted on an empty table."""
    con.execute("DELETE FROM job_sources")
    con.execute("DELETE FROM jobs")
    con.commit()


def test_invalidate_failing_deal_breakers_moves_only_active_failures(con):
    _empty(con)
    con.executemany(
        "INSERT INTO jobs (job_id,title,company,status,deal_breakers_result) VALUES (?,?,?,?,?)",
        [
            ("inv-fail", "Fails", "C", "active", 1),
            ("inv-pass", "Passes", "C", "active", 0),
            ("inv-none", "Unevaluated", "C", "active", None),
            ("inv-already", "Already", "C", "invalidated", 1),
            ("inv-rejected", "Rejected", "C", "rejected", 1),
        ],
    )
    con.commit()

    changed = repositories.invalidate_failing_deal_breakers(con)

    assert changed == 1
    statuses = dict(con.execute("SELECT job_id, status FROM jobs WHERE job_id LIKE 'inv-%'"))
    assert statuses == {
        "inv-fail": "invalidated",
        "inv-pass": "active",  # passed the deal breakers
        "inv-none": "active",  # no verdict yet, so nothing was failed
        "inv-already": "invalidated",
        "inv-rejected": "rejected",  # the word filter got there first
    }


def test_invalidate_failing_deal_breakers_is_idempotent(con):
    _empty(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,status,deal_breakers_result) VALUES ('inv-twice','T','C','active',1)"
    )
    con.commit()

    assert repositories.invalidate_failing_deal_breakers(con) == 1
    # already invalidated, so there is nothing left to change
    assert repositories.invalidate_failing_deal_breakers(con) == 0


def test_invalidate_failing_deal_breakers_matches_the_int_verdict(con):
    """The column holds 1 for a failure; 0 must not be swept up with it."""
    _empty(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,status,deal_breakers_result) "
        "VALUES ('inv-zero','Passes','C','active',0)"
    )
    con.commit()

    assert repositories.invalidate_failing_deal_breakers(con) == 0
    assert repositories.get_job(con, "inv-zero")["status"] == "active"


def test_list_sources_grouped(con):
    con.execute("INSERT INTO jobs (job_id,title,company) VALUES ('repo-src','T','C')")
    con.executemany(
        "INSERT INTO job_sources (job_id,site,source_id,url) VALUES (?,?,?,?)",
        [("repo-src", "linkedin", "s1", "https://x/1"), ("repo-src", "linkedin", "s2", "https://x/2")],
    )
    con.commit()

    grouped = repositories.list_sources_grouped(con)
    assert grouped["repo-src"] == [("linkedin", "https://x/1"), ("linkedin", "https://x/2")]
    assert repositories.get_job_sources(con, "repo-src") == grouped["repo-src"]
