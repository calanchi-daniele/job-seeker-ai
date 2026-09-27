"""Step 3: the local-LLM evaluation and how its failures are handled.

No model is called. The point of these tests is the *failure* contract: a
failed evaluation must raise, and must leave the job pending for a later pass
rather than marking it enriched with an empty verdict.
"""

import pytest

from job_seeker_ai import evaluators, repositories
from job_seeker_ai.exceptions import EnrichmentError
from job_seeker_ai.services import enrichment


class _BrokenCompletions:
    def parse(self, **kwargs):
        raise RuntimeError("connection refused")


class _BrokenChat:
    completions = _BrokenCompletions()


class _BrokenClient:
    chat = _BrokenChat()


def _job(job_id="en-1"):
    return {
        "job_id": job_id,
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "We use C#.",
    }


def _pending_job(con, job_id):
    con.execute(
        "INSERT INTO jobs (job_id,title,company,description,status) VALUES (?,?,?,?, 'active')",
        (job_id, "Backend Engineer", "Acme", "We use C#."),
    )
    con.commit()


def _reset_jobs(con):
    """The database is shared across the whole session and ``enrich_jobs()``
    walks *every* pending active job, so start each service test from a known
    state instead of inheriting rows from earlier tests."""
    con.execute("UPDATE jobs SET enriched_at=NULL, deal_breakers_result=NULL, deal_breakers_reason=NULL")
    con.commit()


# --- evaluators ----------------------------------------------------------


def test_evaluate_raises_instead_of_returning_a_sentinel(monkeypatch):
    """The bug: it returned {"success": False, ...}, which the caller stored
    as an empty verdict and marked the job done."""
    monkeypatch.setattr(evaluators, "client", _BrokenClient())

    with pytest.raises(EnrichmentError) as excinfo:
        evaluators.evaluate_deal_breakers(_job())

    assert "en-1" in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)


def test_evaluate_error_keeps_the_original_cause(monkeypatch):
    monkeypatch.setattr(evaluators, "client", _BrokenClient())

    with pytest.raises(EnrichmentError) as excinfo:
        evaluators.evaluate_deal_breakers(_job())

    assert isinstance(excinfo.value.__cause__, RuntimeError)


# --- the service ---------------------------------------------------------


def test_failed_evaluation_leaves_the_job_pending(con, monkeypatch):
    _reset_jobs(con)
    _pending_job(con, "en-fail")

    def boom(_job_data):
        raise EnrichmentError("no verdict")

    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", boom)

    enrichment.enrich_jobs()

    row = con.execute("SELECT enriched_at, deal_breakers_result FROM jobs WHERE job_id='en-fail'").fetchone()
    assert row == (None, None)
    # and it is offered to the next pass
    assert "en-fail" in [j["job_id"] for j in repositories.get_pending_jobs(con)]


def test_failed_evaluation_does_not_stamp_anything(con, monkeypatch):
    """A rejected verdict must not masquerade as a result for any row."""
    _reset_jobs(con)

    def boom(_job_data):
        raise EnrichmentError("no verdict")

    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", boom)

    enrichment.enrich_jobs()

    assert con.execute("SELECT count(*) FROM jobs WHERE enriched_at IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM jobs WHERE deal_breakers_result IS NOT NULL").fetchone()[0] == 0


def test_successful_evaluation_is_saved_and_clears_pending(con, monkeypatch):
    _reset_jobs(con)
    _pending_job(con, "en-ok")

    def verdict(_job_data):
        return {
            "fails_deal_breakers": True,
            "deal_breakers": [evaluators.DealBreakerItem(criterion="Remote from Italy", score=0, reason="Spain only")],
        }

    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", verdict)

    enrichment.enrich_jobs()

    row = con.execute(
        "SELECT deal_breakers_result, deal_breakers_reason, enriched_at FROM jobs WHERE job_id='en-ok'"
    ).fetchone()
    assert row[0] == 1  # sqlite has no bool
    assert "Remote from Italy: 0 - Spain only" in row[1]
    assert row[2] is not None
    assert "en-ok" not in [j["job_id"] for j in repositories.get_pending_jobs(con)]


def test_clean_verdict_is_stored_as_not_failing(con, monkeypatch):
    _reset_jobs(con)
    _pending_job(con, "en-clean")

    def verdict(_job_data):
        return {"fails_deal_breakers": False, "deal_breakers": []}

    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", verdict)

    enrichment.enrich_jobs()

    assert con.execute("SELECT deal_breakers_result FROM jobs WHERE job_id='en-clean'").fetchone()[0] == 0


def test_verdict_contract_is_1_for_fail_and_0_for_pass(con, monkeypatch):
    """The contract the review site's pills are built on: 1 = failed at least
    one deal breaker, 0 = passed. Inverting either side breaks the other."""
    _reset_jobs(con)
    _pending_job(con, "en-int-fail")
    _pending_job(con, "en-int-pass")

    outcomes = {"en-int-fail": True, "en-int-pass": False}
    monkeypatch.setattr(
        enrichment.evaluators,
        "evaluate_deal_breakers",
        lambda job: {"fails_deal_breakers": outcomes.get(job["job_id"], False), "deal_breakers": []},
    )

    enrichment.enrich_jobs()

    rows = dict(
        con.execute("SELECT job_id, deal_breakers_result FROM jobs WHERE job_id IN ('en-int-fail','en-int-pass')")
    )
    assert rows == {"en-int-fail": 1, "en-int-pass": 0}
    # integers, not bools: the column feeds a numeric comparison in the UI
    assert type(rows["en-int-fail"]) is int
    assert type(rows["en-int-pass"]) is int


def test_the_contract_constants_match_that_mapping():
    assert enrichment.DEAL_BREAKERS_FAILED == 1
    assert enrichment.DEAL_BREAKERS_PASSED == 0


def test_a_response_without_a_verdict_is_never_stored_as_a_pass(con, monkeypatch):
    """Defensive: writing 0 for a missing verdict would show as a green OK."""
    _reset_jobs(con)
    _pending_job(con, "en-nokey")

    monkeypatch.setattr(
        enrichment.evaluators,
        "evaluate_deal_breakers",
        lambda job: {"deal_breakers": []},
    )

    enrichment.enrich_jobs()

    row = con.execute("SELECT deal_breakers_result, enriched_at FROM jobs WHERE job_id='en-nokey'").fetchone()
    assert row == (None, None)


# --- the end-of-pass reconciliation --------------------------------------
#
# enrich_jobs() finishes by flipping every *active* job that failed a deal
# breaker to 'invalidated', so these start from an empty database to make the
# outcome unambiguous.


def _clear(con):
    con.execute("DELETE FROM job_sources")
    con.execute("DELETE FROM jobs")
    con.commit()


def _status(con, job_id):
    return con.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]


def _verdict_is(fails):
    return lambda job: {"fails_deal_breakers": fails, "deal_breakers": []}


def test_failing_a_deal_breaker_invalidates_the_job(con, monkeypatch):
    _clear(con)
    _pending_job(con, "en-inv")
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", _verdict_is(True))

    enrichment.enrich_jobs()

    assert _status(con, "en-inv") == "invalidated"


def test_passing_a_deal_breaker_leaves_the_job_active(con, monkeypatch):
    _clear(con)
    _pending_job(con, "en-keep")
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", _verdict_is(False))

    enrichment.enrich_jobs()

    assert _status(con, "en-keep") == "active"


def test_a_job_with_no_verdict_is_not_invalidated(con, monkeypatch):
    """An unevaluated job has not failed anything - it must stay in play."""
    _clear(con)
    _pending_job(con, "en-uneval")
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", lambda job: {"deal_breakers": []})

    enrichment.enrich_jobs()

    assert _status(con, "en-uneval") == "active"


def test_a_rejected_job_keeps_its_status(con, monkeypatch):
    """The word filter already ruled it out; only 'active' rows move."""
    _clear(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,description,status,deal_breakers_result) "
        "VALUES ('en-rejected','T','C','d','rejected',1)"
    )
    con.commit()
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", _verdict_is(True))

    enrichment.enrich_jobs()

    assert _status(con, "en-rejected") == "rejected"


def test_an_already_invalidated_job_is_left_alone(con, monkeypatch):
    _clear(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,description,status,deal_breakers_result,enriched_at) "
        "VALUES ('en-already','T','C','d','invalidated',1,datetime('now'))"
    )
    con.commit()
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", _verdict_is(True))

    enrichment.enrich_jobs()

    assert _status(con, "en-already") == "invalidated"


def test_reconciliation_also_runs_with_nothing_pending(con, monkeypatch):
    """Heals a verdict a previous run wrote but never acted on."""
    _clear(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,description,status,deal_breakers_result,enriched_at) "
        "VALUES ('en-stale','T','C','d','active',1,datetime('now'))"
    )
    con.commit()
    monkeypatch.setattr(
        enrichment.evaluators,
        "evaluate_deal_breakers",
        lambda job: pytest.fail("nothing is pending, so nothing should be evaluated"),
    )

    enrichment.enrich_jobs()

    assert _status(con, "en-stale") == "invalidated"


def test_a_manual_reactivation_is_undone_on_the_next_pass(con, monkeypatch):
    """Documents a deliberate consequence, not an accident.

    The reconciliation is a bulk update with no memory of the review site's
    `reactivate` button, so a job that still fails a deal breaker comes back as
    invalidated. Narrow the predicate in
    repositories.invalidate_failing_deal_breakers if that should not hold.
    """
    _clear(con)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,description,status,deal_breakers_result,enriched_at) "
        "VALUES ('en-reactivated','T','C','d','active',1,datetime('now'))"
    )
    con.commit()
    monkeypatch.setattr(
        enrichment.evaluators,
        "evaluate_deal_breakers",
        lambda job: pytest.fail("nothing is pending, so nothing should be evaluated"),
    )

    enrichment.enrich_jobs()

    assert _status(con, "en-reactivated") == "invalidated"


def test_reconciling_twice_changes_nothing_the_second_time(con, monkeypatch):
    _clear(con)
    _pending_job(con, "en-idem")
    monkeypatch.setattr(enrichment.evaluators, "evaluate_deal_breakers", _verdict_is(True))

    enrichment.enrich_jobs()
    con.execute("UPDATE jobs SET enriched_at=NULL WHERE job_id='en-idem'")
    con.commit()
    enrichment.enrich_jobs()

    assert _status(con, "en-idem") == "invalidated"
