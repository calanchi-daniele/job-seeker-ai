"""Step 3: evaluate pending jobs with the local LLM and store the verdicts.

Owns the whole lifecycle so a single command is enough: load the model, walk
the pending jobs, save each result, unload the model. The unload happens in a
``finally`` block because a half-finished run must not leave a model
resident in RAM.

The pass then reconciles statuses: no job may stay ``active`` while it fails a
deal breaker, so the model's verdict takes effect in the same command instead
of waiting for a manual pass in the review site. That is a bulk update against
the whole table, not just the jobs this pass looked at - see docs/pipeline.md
for what that means for a job reactivated by hand.

Only deal breakers are evaluated. The culture-scoring criteria (and the columns
they used to feed) were removed to keep the project lean; re-add them together
with the code that populates them.
"""

import time

from job_seeker_ai import evaluators, llm_clients, repositories
from job_seeker_ai.exceptions import EnrichmentError
from job_seeker_ai.storages import connect

# Written by one enrichment pass.
ENRICHED_FIELDS = ("deal_breakers_result", "deal_breakers_reason")

# The deal_breakers_result contract, in both directions: 1 means the posting
# fails at least one deal breaker, 0 means it passes. The review site renders
# these as its pills and this module invalidates on the 1s, so every consumer
# has to agree - see docs/review-website.md. Named rather than written as bare
# 1/0 so that inverting the meaning has to be deliberate.
DEAL_BREAKERS_FAILED = 1
DEAL_BREAKERS_PASSED = 0


def enrich_jobs():
    """Evaluate everything pending, then take the failures out of the running."""
    con = connect()
    jobs = repositories.get_pending_jobs(con)
    con.close()

    if jobs:
        _enrich_pending(jobs)
    else:
        print("nothing pending. run: python -m job_seeker_ai filter (if not run yet), then python -m job_seeker_ai run")

    # Runs even when there was nothing pending: it also cleans up a verdict that
    # a previous run wrote but never acted on.
    _invalidate_deal_breaker_failures()


def _enrich_pending(jobs):
    """Evaluate each job in turn, saving verdicts as they arrive."""
    succeeded = failed = 0
    for j in jobs:
        try:
            res = evaluators.evaluate_deal_breakers(j)
        except EnrichmentError as e:
            # No verdict: leave the row completely untouched so it stays
            # pending and is retried on the next pass. Previously this stored a
            # NULL verdict and stamped enriched_at, which hid the job from
            # every later pass forever.
            failed += 1
            print(f"  !! no verdict for {j['job_id']}, left pending: {e}")
            continue

        if "fails_deal_breakers" not in res:
            # Defensive: an absent verdict must never be stored, because the
            # only values this column has are "failed" and "passed" - writing
            # either would be inventing a result.
            failed += 1
            print(f"  !! no verdict for {j['job_id']}, left pending: response had no fails_deal_breakers")
            continue

        # 1 = fails, 0 = passes (the review site's pills depend on this).
        verdict = DEAL_BREAKERS_FAILED if res["fails_deal_breakers"] else DEAL_BREAKERS_PASSED

        deal_breakers = res.get("deal_breakers")
        if isinstance(deal_breakers, list):
            deal_breakers = "\n".join(f"{x.criterion}: {x.score} - {x.reason}" for x in deal_breakers)

        data = (verdict, deal_breakers, j["job_id"])

        con = connect()
        if repositories.save_job_data(con, data, ENRICHED_FIELDS):
            succeeded += 1
            print(f"  -> Saved (deal_breakers_result: {verdict}, reasons: \n{deal_breakers}\n)")
        else:
            print(f"  !! Failed to update DB for {j['job_id']}")
        con.close()

    print(f"\n{succeeded} enriched, {failed} left pending out of {len(jobs)}")


def _invalidate_deal_breaker_failures():
    """Flip every active job that failed a deal breaker to ``invalidated``."""
    con = connect()
    invalidated = repositories.invalidate_failing_deal_breakers(con)
    con.close()

    if invalidated:
        print(f"{invalidated} invalidated for failing a deal breaker")


def start_enrich_process():
    """Load the model, enrich everything pending, always unload afterwards."""
    try:
        llm_clients.stop_agent()
        llm_clients.start_agent()
        enrich_jobs()
    finally:
        time.sleep(3)
        llm_clients.stop_agent()
