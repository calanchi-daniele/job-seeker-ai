"""Pure text-parsing helpers: salary extraction and the dedupe key.

No I/O, no database, no network - every function here takes a string and
returns a string (or ``None``), which is what makes them cheap to test.
"""

import hashlib
import re

SALARY_KEYWORD_RE = re.compile(
    r"(?:salary|compensation|comp\.?\s*range|pay\s*range|base\s*pay|"
    r"\bRAL\b|retribuzione|stipendio)[^\n]{0,80}",
    re.I,
)
AMOUNT_RE = re.compile(
    r"[$€£]\s?\d[\d.,]*\s?[kK]?(?:\s*[-–to]{1,3}\s*[$€£]?\s?\d[\d.,]*\s?[kK]?)?"
    r"(?:\s*(?:per|/|a)\s*(?:year|yr|annum|month|mo|hour|hr|day))?"
    r"|\d[\d.,]*\s?[kK]?\s*[-–]\s*\d[\d.,]*\s?[kK]?\s*(?:EUR|USD|GBP)\b"
)


def find_salary(text_block):
    """Only trust amounts near a salary/compensation keyword. No keyword, no guess.

    Prose salary mentions (the common case) work well. LinkedIn's
    structured "preferences" sidebar widget scatters label/value across
    separate lines/elements and isn't parsed - the enrichment step still
    sees the full description, so nothing is lost, just not auto-filled.
    Revisit if that widget becomes the primary salary source.
    """
    if not text_block:
        return None
    first_kw = None
    for m in SALARY_KEYWORD_RE.finditer(text_block):
        first_kw = first_kw or m.group(0).strip()
        amt = AMOUNT_RE.search(m.group(0))
        if amt:
            return amt.group(0).strip()
    return first_kw


def normalize_key(title, company, location):
    """Cheap, free, exact-match dedupe key: same job re-posted or found by two
    overlapping searches.

    Does NOT catch near-duplicates (slightly reworded titles) - that needs a
    fuzzy pass, deliberately not built yet since the observed duplicate
    problem (cross-search overlap) is always an exact
    title+company+location match. Add difflib.SequenceMatcher scoring plus
    an LLM ambiguous-pair prompt if fuzzy near-duplicates start showing up in
    practice.
    """
    parts = [re.sub(r"[^\w]+", " ", (s or "").lower()).strip() for s in (title, company, location)]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
