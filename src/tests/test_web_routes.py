"""The review website's routes.

Skipped, not failed, when the web extra is not installed: the web layer is an
optional step and ``pip install -e .[dev]`` is what brings it in.
"""

import pytest

pytest.importorskip("fastapi", reason="web extra not installed (pip install -e .[dev])")
pytest.importorskip("jinja2", reason="web extra not installed (pip install -e .[dev])")
pytest.importorskip("httpx", reason="web extra not installed (pip install -e .[dev])")

from fastapi.testclient import TestClient  # noqa: E402

from job_seeker_ai.web.app import app  # noqa: E402


def _seed(con):
    """Idempotent: every test in this module seeds the same two jobs into the
    one shared throwaway database, so re-seeding must not collide.

    ``scraped_at`` is explicit so the "newest first" ordering is deterministic.
    """
    con.executemany(
        "INSERT OR REPLACE INTO jobs (job_id,title,company,location,status,scraped_at) VALUES (?,?,?,?,?,?)",
        [
            ("web-hi", "Senior C# Engineer", "Acme", "Remote", "active", "2026-01-02 00:00:00"),
            ("web-lo", "Java Developer", "Beta", "Rome", "active", "2026-01-01 00:00:00"),
        ],
    )
    con.execute(
        "INSERT OR IGNORE INTO job_sources (job_id,site,source_id,url) VALUES ('web-hi','linkedin','s1','https://x/1')"
    )
    con.commit()


def test_list_renders_seeded_jobs(con):
    _seed(con)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text


def test_list_renders_the_source_link(con):
    _seed(con)
    response = TestClient(app).get("/")
    assert "https://x/1" in response.text


def test_list_is_newest_first(con):
    _seed(con)
    body = TestClient(app).get("/").text
    assert body.index("Senior C# Engineer") < body.index("Java Developer")


def test_list_labels_the_ordering_as_added(con):
    """The old "posted" sort label was misleading - it ordered by scraped_at."""
    _seed(con)
    response = TestClient(app).get("/")
    assert "order: Added" in response.text
    assert 'name="sort"' not in response.text
    assert "salary est." not in response.text
    assert "min params" not in response.text


def test_removed_query_params_are_ignored(con):
    """min_cv/min_params/sort used to filter and order the list; they are gone
    with the score columns, and must not break the page."""
    _seed(con)
    response = TestClient(app).get("/", params={"min_cv": "5", "min_params": "5", "sort": "cv_score"})
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text


def _set_verdict(con, job_id, value):
    con.execute("UPDATE jobs SET deal_breakers_result = ? WHERE job_id = ?", (value, job_id))
    con.commit()


def _reset(con):
    """Empty the shared database.

    The list page renders *every* row, and earlier test modules leave jobs with
    verdicts of their own, so a pill can only be counted exactly on its own.
    """
    con.execute("DELETE FROM job_sources")
    con.execute("DELETE FROM jobs")
    con.commit()


def test_deal_breaker_pill_is_pending_when_null(con):
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", None)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--pending"' in body
    assert ">Pending<" in body


def test_deal_breaker_pill_is_ok_when_zero(con):
    """0 means it passed - which used to render as a blank cell, because 0 is
    falsy and the template did `result or ''`."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 0)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--ok"' in body
    assert ">OK<" in body


def test_deal_breaker_pill_is_fail_when_one(con):
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)

    body = TestClient(app).get("/").text

    assert 'class="pill pill--fail"' in body
    assert ">Fail<" in body


def test_deal_breaker_pill_states_are_distinct_per_row(con):
    """One row failing must not paint the others."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)
    _set_verdict(con, "web-lo", 0)

    body = TestClient(app).get("/").text

    assert body.count(">Fail<") == 1
    assert body.count(">OK<") == 1
    assert body.count(">Pending<") == 0


def test_deal_breaker_pill_has_a_tooltip(con):
    """Colour alone is not an accessible label; each pill states its meaning."""
    _reset(con)
    _seed(con)
    _set_verdict(con, "web-hi", 1)

    body = TestClient(app).get("/").text

    assert "Fails at least one deal breaker" in body


def test_stylesheet_defines_the_pill_modifiers(con):
    """The pills are only coloured if the stylesheet keeps these rules."""
    css = TestClient(app).get("/static/css/style.css").text

    for modifier in (".pill--ok", ".pill--fail", ".pill--pending"):
        assert modifier in css


def test_status_filter_excludes_active_jobs(con):
    _seed(con)
    response = TestClient(app).get("/", params={"status": "rejected"})
    assert "Senior C# Engineer" not in response.text


def test_unknown_job_redirects_back_to_the_list(con):
    response = TestClient(app, follow_redirects=False).get("/jobs/does-not-exist")
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_detail_page_renders_the_job(con):
    _seed(con)
    response = TestClient(app).get("/jobs/web-hi")
    assert response.status_code == 200
    assert "Senior C# Engineer" in response.text
    assert "Acme" in response.text


def test_invalidate_then_reactivate(con):
    _seed(con)
    client = TestClient(app, follow_redirects=False)

    assert client.post("/jobs/web-hi/invalidate").status_code == 303
    assert con.execute("SELECT status FROM jobs WHERE job_id='web-hi'").fetchone()[0] == "invalidated"

    assert client.post("/jobs/web-hi/reactivate").status_code == 303
    assert con.execute("SELECT status FROM jobs WHERE job_id='web-hi'").fetchone()[0] == "active"


def test_static_stylesheet_is_served(con):
    response = TestClient(app).get("/static/css/style.css")
    assert response.status_code == 200
    assert "font-family" in response.text
