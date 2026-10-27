"""Step 3: evaluate pending jobs with the model API and store the verdicts.

One command does a whole pass: check the client configuration, walk the pending
jobs, save each verdict, then reconcile statuses. There is no lifecycle to own -
the endpoint is external and stays up between runs - so an interrupted pass
leaves nothing running behind it.

Evaluations are fanned out concurrently - ``config.LLM_MAX_CONCURRENCY`` of them
at a time - because each one is a short round trip to an HTTP endpoint. Database
writes are serialised, so the concurrency is confined to the model calls.

The pass then reconciles statuses: no job may stay ``active`` while it fails a
deal breaker, so the model's verdict takes effect in the same command instead
of waiting for a manual pass in the review site. That is a bulk update against
the whole table, not just the jobs this pass looked at - see docs/pipeline.md
for what that means for a job reactivated by hand.

Only deal breakers are evaluated. The culture-scoring criteria (and the columns
they used to feed) were removed to keep the project lean; re-add them together
with the code that populates them.
"""

import asyncio

from job_seeker_ai import config, evaluators, llm_clients, repositories
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


async def enrich_jobs():
    """Evaluate everything pending, then take the failures out of the running."""
    llm_clients.check_config()

    con = connect()
    jobs = repositories.get_pending_jobs(con)
    con.close()

    if jobs:
        await _enrich_pending(jobs)
    else:
        print("nothing pending. run: python -m job_seeker_ai filter (if not run yet), then python -m job_seeker_ai run")

    # Runs even when there was nothing pending: it also cleans up a verdict that
    # a previous run wrote but never acted on.
    _invalidate_deal_breaker_failures()


async def _enrich_pending(jobs):
    """Evaluate the pending jobs concurrently, then report the outcome.

    Everything is queued at once and the semaphore decides how many are in
    flight, so the fast jobs do not wait behind the slow ones.
    """
    limit = config.LLM_MAX_CONCURRENCY
    print(f"Evaluating {len(jobs)} job(s), {limit} at a time")

    semaphore = asyncio.Semaphore(limit)
    db_lock = asyncio.Lock()

    saved = await asyncio.gather(*(_evaluate_and_save(j, semaphore, db_lock) for j in jobs))

    succeeded = sum(1 for was_saved in saved if was_saved)
    print(f"\n{succeeded} enriched, {len(jobs) - succeeded} left pending out of {len(jobs)}")


async def _evaluate_and_save(job, semaphore, db_lock) -> bool:
    """Evaluate one job and store its verdict. ``False`` means left pending.

    Only the semaphore limits how long this waits, so a single failure never
    blocks or discards the others.
    """
    async with semaphore:
        try:
            res = await evaluators.evaluate_deal_breakers(job)
        except EnrichmentError as e:
            # No verdict: leave the row completely untouched so it stays pending
            # and is retried on the next pass. Storing a NULL verdict and
            # stamping enriched_at would hide the job from every later pass.
            print(f"  !! no verdict for {job['job_id']}, left pending: {e}")
            return False

        if "fails_deal_breakers" not in res:
            # Defensive: an absent verdict must never be stored, because the only
            # values this column has are "failed" and "passed" - writing either
            # would be inventing a result.
            print(f"  !! no verdict for {job['job_id']}, left pending: response had no fails_deal_breakers")
            return False

        # 1 = fails, 0 = passes (the review site's pills depend on this).
        verdict = DEAL_BREAKERS_FAILED if res["fails_deal_breakers"] else DEAL_BREAKERS_PASSED

        deal_breakers = res.get("deal_breakers")
        if isinstance(deal_breakers, list):
            deal_breakers = "\n".join(f"{x.criterion}: {x.score} - {x.reason}" for x in deal_breakers)

        data = (verdict, deal_breakers, job["job_id"])

        # Serialised: the model calls are what benefit from running together, and
        # one writer at a time keeps SQLite out of the picture entirely.
        async with db_lock:
            con = connect()
            stored = repositories.save_job_data(con, data, ENRICHED_FIELDS)
            con.close()

        if stored:
            print(f"  -> Saved {job['job_id']} (deal_breakers_result: {verdict})")
        else:
            print(f"  !! Failed to update DB for {job['job_id']}")
        return stored


def _invalidate_deal_breaker_failures():
    """Flip every active job that failed a deal breaker to ``invalidated``."""
    con = connect()
    invalidated = repositories.invalidate_failing_deal_breakers(con)
    con.close()

    if invalidated:
        print(f"{invalidated} invalidated for failing a deal breaker")
