"""Enrichment: prompt composition and the structured verdict model.

No LLM call is made here - these cover the wiring around the model, which is
the part that can be verified without a running LM Studio.
"""

from job_seeker_ai import criteria_loaders, evaluators


def test_deal_breakers_criteria_file_is_loadable():
    text = criteria_loaders.load_deal_breakers()
    assert "Remote from Italy" in text
    assert "Match min salary" in text


def test_prompt_embeds_the_criteria_file():
    text = criteria_loaders.load_deal_breakers()
    prompt = evaluators.DEAL_BREAKERS_PROMPT.format(deal_breakers=text)
    assert "Remote from Italy" in prompt
    # the placeholder must actually be substituted, not left literal
    assert "{deal_breakers}" not in prompt


def test_verdict_model_parses_a_model_style_payload():
    payload = {
        "deal_breakers": [
            {"criterion": "Remote from Italy", "score": 0, "reason": "requires residence in Spain"},
        ]
    }
    evaluation = evaluators.DealBreakersEvaluation(**payload)
    assert evaluation.deal_breakers[0].score == 0
    assert evaluation.deal_breakers[0].criterion == "Remote from Italy"


def test_verdict_model_defaults_score_to_zero():
    item = evaluators.DealBreakerItem(criterion="c", reason="r")
    assert item.score == 0
