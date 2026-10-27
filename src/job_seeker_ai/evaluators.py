"""Step 3: LLM evaluation of the strict deal breakers.

Turns one pending job plus ``criteria/deal_breakers.md`` into a structured
verdict. The model is asked for a typed object rather than free text so the
result can be stored without a parsing layer.

Nothing here knows which endpoint answers. ``llm_clients.complete_structured``
owns the endpoint, the credential and the provider quirks, so this module only
builds the prompt and interprets the verdict.

The prompt deliberately asks for verbatim quotes: it is easier to spot a
hallucinated verdict when the model has to cite the posting. It also spells out
the JSON shape, because an endpoint without strict structured outputs falls back
to ``json_object`` mode, where the schema is a request rather than a constraint.

The call is ``async`` so the enrichment pass can run several at once; how many is
``config.LLM_MAX_CONCURRENCY``, and the fan-out itself lives in
``services/enrichment.py``.
"""

import time

from pydantic import BaseModel, Field

from job_seeker_ai import criteria_loaders, llm_clients
from job_seeker_ai.exceptions import EnrichmentError

DEAL_BREAKERS_PROMPT = """You are a strict job filtering assistant.
Read the job description and evaluate it against the Deal Breakers below.

--- DEAL BREAKERS ---
Allowable scores: 0 (Does not meet), 1 (Partial or no evidence found), 2 (Fully meets)
{deal_breakers}

--- CRITICAL INSTRUCTIONS ---
1. Do not assume or guess. Strictly follow the scoring guides.
2. MISSING EVIDENCE RULES: If there is no evidence found for a criterion, you MUST use the default values specified in the guides.

--- CRITICAL FORMATTING RULE ---
1. For DealBreakerItem.reason, provide ONLY the verbatim sentence or direct factual quote from the job posting. 
2. Do NOT append explanations such as "which satisfies rule X" or "violating deal breaker Y".

--- OUTPUT FORMAT ---
Reply with one JSON object and nothing else, with a single key "deal_breakers"
holding one entry per rule. Each entry has "criterion" (the rule name), "score"
(an integer) and "reason" (the quote).
"""


class DealBreakerItem(BaseModel):
    criterion: str = Field(description="The failed deal breaker name")
    score: int = Field(default=0, description="Always 0 for failed rules")
    reason: str = Field(
        description="Direct quote or factual excerpt from the job description regarding the deal breaker"
    )


class DealBreakersEvaluation(BaseModel):
    deal_breakers: list[DealBreakerItem] = Field(
        description="List of ALL dealbreakers with their score and reason for that score."
    )


async def evaluate_deal_breakers(job_data: dict) -> dict:
    """Evaluate one job and return its deal-breaker verdict.

    Await it: the request is asynchronous so the caller can run several at once.
    Raises ``EnrichmentError`` when no usable verdict could be produced - it
    previously returned a ``{"success": False, ...}`` sentinel that the caller
    stored as an empty result, which marked the job as enriched and stopped it
    from ever being retried.
    """
    dynamic_input = f"""JOB ID: {job_data["job_id"]}
                    {job_data["title"]} @ {job_data["company"]} — {job_data["location"]}
                    DESCRIPTION:
                    {job_data["description"]}"""

    print(f"Evaluating deal breakers: {job_data['title']} @ {job_data['company']}...")

    start_time = time.perf_counter()

    try:
        parsed_result = await llm_clients.complete_structured(
            DealBreakersEvaluation,
            [
                {
                    "role": "system",
                    "content": DEAL_BREAKERS_PROMPT.format(deal_breakers=criteria_loaders.load_deal_breakers()),
                },
                {"role": "user", "content": dynamic_input},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
    except Exception as e:
        raise EnrichmentError(f"deal-breaker evaluation failed for job {job_data['job_id']}: {e}") from e

    elapsed = time.perf_counter() - start_time
    print(f"  {job_data['job_id']}: evaluated in {elapsed:.2f}s")

    has_failing_score = any(int(item.score) == 0 for item in parsed_result.deal_breakers)

    return {
        "fails_deal_breakers": has_failing_score,
        "deal_breakers": parsed_result.deal_breakers,
    }
