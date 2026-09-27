"""Step 2: apply the hard word rules to every unfiltered active job.

Cheap, deterministic and LLM-free on purpose: it runs before enrichment so
the model is only ever pointed at postings that can still pass.

Rejection is recorded as a status change plus a reason, never a delete, so
the review site can still show why something was dismissed.
"""

from pathlib import Path

from job_seeker_ai import criteria_loaders, repositories
from job_seeker_ai.filters import evaluate
from job_seeker_ai.storages import connect


def run(
    dry_run: bool = False, must_words_path: str | Path | None = None, forbidden_words_path: str | Path | None = None
):
    """Filter all unfiltered active jobs. Returns ``(kept, rejected)``.

    The two path arguments default to ``criteria/`` and exist so tests can
    point at a scratch rule file instead of the real one.
    """
    must_rules = criteria_loaders.load_must_words(must_words_path)
    forbidden_words = criteria_loaders.load_forbidden_words(forbidden_words_path)

    con = connect()
    rows = repositories.list_unfiltered_active_jobs(con)

    kept = rejected = 0
    for job_id, title, description in rows:
        reason = evaluate(f"{title} {description}", must_rules, forbidden_words)
        if reason:
            rejected += 1
            if not dry_run:
                repositories.reject_job(con, job_id, reason)
        else:
            kept += 1
            if not dry_run:
                repositories.mark_job_filtered(con, job_id)

    if not dry_run:
        con.commit()
    con.close()

    print(f"{kept} kept, {rejected} rejected out of {len(rows)}")
    return kept, rejected
