"""The word filter: pure verdicts, then the database-side run."""

from pathlib import Path

from job_seeker_ai import filters, repositories
from job_seeker_ai.services import filtering


def test_evaluate_passes_when_all_rules_match():
    must = [["net", "c#"], ["remote"]]
    forbidden = [["consultancy"], ["blockchain"]]
    # has "c#" and "remote", no forbidden word
    assert filters.evaluate("Senior C# Engineer, full remote", must, forbidden) is None


def test_evaluate_rejects_on_missing_required_word():
    must = [["net", "c#"], ["remote"]]
    forbidden = [["consultancy"], ["blockchain"]]
    assert filters.evaluate("Java developer, onsite", must, forbidden) is not None


def test_evaluate_rejects_on_forbidden_word():
    must = [["net", "c#"], ["remote"]]
    forbidden = [["consultancy"], ["blockchain"]]
    # must-words match, but a forbidden word appears
    assert filters.evaluate("C# remote role at a consultancy", must, forbidden) is not None


def test_evaluate_reason_names_the_failing_rule():
    reason = filters.evaluate("Java dev", [["net", "c#"]], [])
    assert reason is not None and "net" in reason


def _write_rules(tmp_path: Path) -> tuple[Path, Path]:
    must = tmp_path / "must_words.md"
    forbidden = tmp_path / "forbidden_words.md"
    must.write_text(".NET OR C#\nremote")
    forbidden.write_text("consultancy")
    return must, forbidden


def test_run_rejects_and_keeps(con, tmp_path):
    must, forbidden = _write_rules(tmp_path)
    con.executemany(
        "INSERT INTO jobs (job_id,title,company,norm_key,description,status) VALUES (?,?,?,?,?,'active')",
        [
            ("wf-keep", "Senior C# Engineer", "C1", "k-wfkeep", "full remote .NET role"),
            ("wf-forbidden", "C# Engineer", "C2", "k-wfforbidden", "remote .NET role at a consultancy"),
            ("wf-missing", "Java Engineer", "C3", "k-wfmissing", "onsite Java role"),
        ],
    )
    con.commit()

    filtering.run(must_words_path=must, forbidden_words_path=forbidden)

    rows = dict(con.execute("SELECT job_id, status FROM jobs WHERE job_id IN ('wf-keep','wf-forbidden','wf-missing')"))
    assert rows == {"wf-keep": "active", "wf-forbidden": "rejected", "wf-missing": "rejected"}


def test_run_records_why_it_rejected(con, tmp_path):
    must, forbidden = _write_rules(tmp_path)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,norm_key,description,status) "
        "VALUES ('wf-reason','C# Engineer','C4','k-wfreason','remote .NET at a consultancy','active')"
    )
    con.commit()

    filtering.run(must_words_path=must, forbidden_words_path=forbidden)

    reason = con.execute("SELECT filter_reason FROM jobs WHERE job_id='wf-reason'").fetchone()[0]
    assert reason and "consultancy" in reason


def test_dry_run_writes_nothing(con, tmp_path):
    must, forbidden = _write_rules(tmp_path)
    con.execute(
        "INSERT INTO jobs (job_id,title,company,norm_key,description,status) "
        "VALUES ('wf-dry','Java Engineer','C5','k-wfdry','onsite Java role','active')"
    )
    con.commit()

    filtering.run(dry_run=True, must_words_path=must, forbidden_words_path=forbidden)

    row = con.execute("SELECT status, filtered_at FROM jobs WHERE job_id='wf-dry'").fetchone()
    assert row == ("active", None)


def test_run_is_idempotent(con, tmp_path):
    """Already-filtered jobs are not re-evaluated on a second pass.

    filtering.run() walks *every* unfiltered active job in the (shared)
    throwaway database, so mark any leftovers from earlier tests as already
    filtered first - otherwise this asserts on counts it does not own.
    """
    must, forbidden = _write_rules(tmp_path)
    con.execute("UPDATE jobs SET filtered_at=datetime('now') WHERE filtered_at IS NULL")
    con.execute(
        "INSERT INTO jobs (job_id,title,company,norm_key,description,status,filtered_at) "
        "VALUES ('wf-done','Java Engineer','C6','k-wfdone','onsite Java role','active',datetime('now'))"
    )
    con.commit()

    kept, rejected = filtering.run(must_words_path=must, forbidden_words_path=forbidden)

    assert (kept, rejected) == (0, 0)
    assert repositories.get_job(con, "wf-done")["status"] == "active"
