"""Loading of the hand-edited files under ``criteria/``.

Two shapes are needed:

* **rule lines** - one rule per line, ``word OR word`` alternatives.
  Used by the word filter (must_words.md / forbidden_words.md).
* **raw text** - the whole file is handed to the LLM as prompt material
  (deal_breakers.md).

Reading is the only thing this module does; interpreting the rules is
``filters.py``'s job and turning them into a prompt is ``evaluators.py``'s.

Every read is explicit about UTF-8. The criteria files contain currency
symbols (``€``), and ``read_text()`` without an encoding uses the *locale*
encoding - cp1252 on Windows - which silently turned ``€65,000`` into three
mojibake characters before it reached the LLM prompt.
"""

from pathlib import Path

from job_seeker_ai import config

# The criteria files are hand-edited and contain currency symbols; never let
# the locale decide how to decode them.
ENCODING = "utf-8"


def load_rule_lines(path: str | Path) -> list[list[str]]:
    """Non-empty lines -> list of word lists (split on ``" OR "``).

    Missing file yields no rules, matching the original word-filter
    behaviour: an absent forbidden_words.md rejects nothing.
    """
    path = Path(path)
    if not path.exists():
        return []
    lines = [ln.strip() for ln in path.read_text(encoding=ENCODING).splitlines()]
    return [[w.strip().lower() for w in ln.split(" OR ")] for ln in lines if ln]


def read_criteria_text(path: str | Path) -> str:
    """Whole-file text for prompt material."""
    return Path(path).read_text(encoding=ENCODING)


def load_must_words(path: str | Path | None = None) -> list[list[str]]:
    return load_rule_lines(path or config.MUST_WORDS_PATH)


def load_forbidden_words(path: str | Path | None = None) -> list[list[str]]:
    """Each line is a single-phrase list here, but the shape is identical."""
    return load_rule_lines(path or config.FORBIDDEN_WORDS_PATH)


def load_deal_breakers(path: str | Path | None = None) -> str:
    return read_criteria_text(path or config.DEAL_BREAKERS_PATH)
