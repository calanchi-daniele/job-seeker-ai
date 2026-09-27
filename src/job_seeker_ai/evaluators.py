"""Step 3: LLM evaluation of the strict deal breakers.

Turns one pending job plus ``criteria/deal_breakers.md`` into a structured
verdict. The model is asked for a typed object rather than free text so the
result can be stored without a parsing layer.

The prompt deliberately asks for verbatim quotes: it is easier to spot a
hallucinated verdict when the model has to cite the posting.
"""

import json
import time

from pydantic import BaseModel, Field

from job_seeker_ai import config, criteria_loaders
from job_seeker_ai.exceptions import EnrichmentError
from job_seeker_ai.llm_clients import client

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


def evaluate_deal_breakers(job_data: dict) -> dict:
    """Evaluate one job and return its deal-breaker verdict.

    Raises ``EnrichmentError`` when no usable verdict could be produced. It
    previously returned a ``{"success": False, ...}`` sentinel that the caller
    stored as an empty result, which marked the job as enriched and stopped it
    from ever being retried.
    """
    dynamic_input = f"""JOB ID: {job_data["job_id"]}
                    {job_data["title"]} @ {job_data["company"]} — {job_data["location"]}
                    DESCRIPTION:
                    {job_data["description"]}"""

    print(f"\nEvaluating deal breakers: {job_data['title']} @ {job_data['company']}...")

    start_time = time.perf_counter()

    try:
        response = client.chat.completions.parse(
            model=config.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": DEAL_BREAKERS_PROMPT.format(deal_breakers=criteria_loaders.load_deal_breakers()),
                },
                {"role": "user", "content": dynamic_input},
            ],
            temperature=0.1,
            max_tokens=200,
            response_format=DealBreakersEvaluation,
            extra_body={"enable_thinking": False},
        )

        raw_message = response.choices[0].message

        if raw_message.parsed:
            parsed_result = raw_message.parsed
        else:
            # Fallback: the model put the JSON in reasoning_content instead.
            parsed_dict = json.loads(raw_message.reasoning_content)
            parsed_result = DealBreakersEvaluation(**parsed_dict)
    except Exception as e:
        raise EnrichmentError(f"deal-breaker evaluation failed for job {job_data['job_id']}: {e}") from e

    end_time = time.perf_counter()
    print(f"Evaluation time: {end_time - start_time:.2f} seconds")

    has_failing_score = any(int(item.score) == 0 for item in parsed_result.deal_breakers)

    return {
        "fails_deal_breakers": has_failing_score,
        "deal_breakers": parsed_result.deal_breakers,
    }
