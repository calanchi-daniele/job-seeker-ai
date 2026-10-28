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


# --- deal breakers, as the settings page edits them ----------------------
# The file is one flat list per region, and a sub-item names its parent by key.
# These two functions are the only place that structure is understood, so the
# review site and the prompt cannot drift apart.

PRINCIPLES_HEADER = "--- GENERAL EVALUATION PRINCIPLES ---"
EXAMPLES_HEADER = "--- EXAMPLES OF CORRECT SCORING ---"


def parse_deal_breakers(text: str) -> list[dict]:
    """Flattened Markdown -> one dict per deal breaker.

    Each dict is ``{key, description, principles, examples}``: the first two come
    from the core list, the sub-lists from the two regions under their headers. A
    sub-item joins the deal breaker whose key it repeats, which is why keys have to
    be unique within a file.

    A sub-item whose parent is missing becomes a deal breaker with an empty
    description rather than being dropped - losing a rule silently is worse than
    showing a card with a gap in it.
    """
    items: list[dict] = []
    by_key: dict[str, dict] = {}
    region = "core"

    def parent_for(key: str) -> dict:
        item = by_key.get(key)
        if item is None:
            item = {"key": key, "description": "", "principles": [], "examples": []}
            items.append(item)
            by_key[key] = item
        return item

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == PRINCIPLES_HEADER:
            region = "principles"
            continue
        if line == EXAMPLES_HEADER:
            region = "examples"
            continue
        if not line.startswith("- "):
            continue

        key, _, rest = line[2:].partition(": ")
        key = key.strip()
        if not key:
            continue

        if region == "core":
            item = parent_for(key)
            item["description"] = rest.strip()
            continue

        # The split is on the first " - " only: an example such as
        # "€60,000 - €75,000" keeps the second dash in its own text.
        first, _, second = rest.partition(" - ")
        if region == "principles":
            parent_for(key)["principles"].append({"title": first.strip(), "principle": second.strip()})
        else:
            parent_for(key)["examples"].append({"score": first.strip(), "example": second.strip()})

    return items


def format_deal_breakers(items: list[dict]) -> str:
    """One dict per deal breaker -> the flattened Markdown the prompt reads.

    The inverse of :func:`parse_deal_breakers`, and the shape the LLM has been
    reading all along: a core list, then each sub-list under its header, each line
    prefixed with its deal breaker's key.

    A deal breaker with no key is skipped - it could not own sub-items on the way
    back in - and an entirely empty sub-item is dropped rather than written as a
    bare ``- key:  -`` line.
    """
    core: list[str] = []
    principles: list[str] = []
    examples: list[str] = []

    for item in items:
        key = (item.get("key") or "").strip()
        if not key:
            continue
        core.append(f"- {key}: {(item.get('description') or '').strip()}")

        for principle in item.get("principles") or []:
            title = (principle.get("title") or "").strip()
            body = (principle.get("principle") or "").strip()
            if title or body:
                principles.append(f"- {key}: {title} - {body}")

        for example in item.get("examples") or []:
            score = (example.get("score") or "").strip()
            body = (example.get("example") or "").strip()
            if score or body:
                examples.append(f"- {key}: {score} - {body}")

    lines = list(core)
    if lines:
        lines.append("")
    lines.append(PRINCIPLES_HEADER)
    lines.extend(principles)
    lines.append("")
    lines.append(EXAMPLES_HEADER)
    lines.extend(examples)
    return "\n".join(lines).rstrip("\n") + "\n"


def load_deal_breaker_items(path: str | Path | None = None) -> list[dict]:
    """The deal breakers as the settings page edits them.

    A missing file yields no items, the same way ``load_rule_lines`` treats a
    missing word list.
    """
    path = Path(path or config.DEAL_BREAKERS_PATH)
    if not path.exists():
        return []
    return parse_deal_breakers(path.read_text(encoding=ENCODING))
