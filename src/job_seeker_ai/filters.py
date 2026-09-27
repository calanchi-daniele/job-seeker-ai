"""Step 2 decision logic: hard word filter, no LLM.

Rejects jobs before they ever reach enrichment, so the LLM only spends
time and searches on jobs that can pass.

* ``criteria/must_words.md`` - one rule per line, ``word OR word OR ...``.
  A job must match at least one word on EVERY line (AND across lines, OR
  within a line).
* ``criteria/forbidden_words.md`` - one word/phrase per line. A job is
  rejected if ANY of them appears.

Matching is plain case-insensitive substring on title+description - good
enough for word/phrase lists like ``.NET``, ``C#``, ``forward deployed``
that mix alnum and punctuation (a word-boundary regex would mishandle
``C#`` and ``.NET``).

This module is pure: it takes text and rules and returns a verdict. The
database orchestration around it lives in ``services/filtering.py``.
"""


def evaluate(text, must_rules, forbidden_words):
    """Return ``None`` if the job passes, else a short human-readable reason."""
    text = (text or "").lower()
    for w in forbidden_words:
        if w[0] in text:
            return f"forbidden word matched: {w[0]}"
    for words in must_rules:
        if not any(w in text for w in words):
            return f"no match for required: {' OR '.join(words)}"
    return None
