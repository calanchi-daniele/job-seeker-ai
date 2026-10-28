"""The job-sites search file: one JSON document per site, under ``job_sites/``.

``job_sites/linkedin.json`` holds the searches the scrape step runs, and the
document has two keys that both matter:

* ``_legend`` - the allowed values for the constrained search parameters, plus
  ``_multiple``, which names the ones that take several at once. It is
  documentation *and* the schema the review site builds its form from, which is why
  a save merges into the existing document rather than rewriting it.
* ``searches`` - the list itself, one entry per search.

Only this module knows that shape. ``services/scraping`` turns the entries into
``SearchSpec`` objects for an adapter, and the settings page edits them as cards;
neither one touches the JSON.
"""

import json
from pathlib import Path

from job_seeker_ai import config
from job_seeker_ai.criteria_loaders import ENCODING

SEARCHES_KEY = "searches"
LEGEND_KEY = "_legend"
MULTIPLE_KEY = "_multiple"


def load_document(path: str | Path | None = None, *, missing_ok: bool = False) -> dict:
    """The whole document, unmodified.

    Raises loudly on a missing file or bad JSON, the same reason the scrape step
    does: a silent empty run is indistinguishable from "no new jobs". ``missing_ok``
    is for the settings page, which has to open on a fresh checkout so the first
    search can be created.
    """
    target = Path(path or config.SEARCHES_PATH)
    if missing_ok and not target.exists():
        return {}
    return json.loads(target.read_text(encoding=ENCODING))


def _legend(document: dict) -> dict:
    """The allowed-value lists, without the ``_``-prefixed documentation keys.

    ``_legend._comment`` explains the file inside the file and ``_legend._multiple``
    names the parameters that accept several values; neither is a value list, so
    neither may turn into a field in the form.
    """
    return {key: values for key, values in (document.get(LEGEND_KEY) or {}).items() if not key.startswith("_")}


def _multiple_keys(document: dict) -> set[str]:
    """The parameters that take several values at once, per ``_legend._multiple``.

    Multiplicity belongs to the *parameter*, not to whatever is stored for it right
    now: a search that has never set ``experience_levels`` still has to be offered
    checkboxes, or the first choice made in the form is the only one it can hold.
    """
    return set((document.get(LEGEND_KEY) or {}).get(MULTIPLE_KEY) or [])


def parameter_keys(document: dict) -> list[str]:
    """Every search parameter a card shows, in a stable order.

    The parameters already present in the data come first - ``keywords`` and
    ``location`` are free text and are not in the legend, so a legend-only field list
    would hide the two that matter most - followed by any legend key no search uses
    yet. That keeps a hand-added parameter editable instead of silently dropping it
    on the next save.
    """
    seen: list[str] = []
    for search in document.get(SEARCHES_KEY) or []:
        for key in search.get("params") or {}:
            if key not in seen:
                seen.append(key)
    return seen + [key for key in _legend(document) if key not in seen]


def _card(search: dict, keys: list[str], legend: dict, multi: set[str]) -> dict:
    """One search as the template needs it: name, pages, and resolved fields."""
    params = search.get("params") or {}
    fields = []
    for key in keys:
        stored = params.get(key, "")
        # One value and a list of them both feed a multi-select, so a parameter
        # declared multi keeps working after it is widened.
        chosen = stored if isinstance(stored, list) else ([] if stored == "" else [stored])
        multiple = key in multi or isinstance(stored, list)
        fields.append(
            {
                "key": key,
                # A legend entry is the allowed list; anything else is free text.
                "options": legend.get(key),
                "multiple": multiple,
                "value": stored if isinstance(stored, str) else "",
                "selected": [str(item) for item in chosen],
            }
        )
    return {"name": search.get("name", ""), "pages": search.get("pages", 1), "fields": fields}


def search_cards(document: dict) -> list[dict]:
    """One card per search."""
    keys = parameter_keys(document)
    legend = _legend(document)
    multi = _multiple_keys(document)
    return [_card(search, keys, legend, multi) for search in document.get(SEARCHES_KEY) or []]


def blank_card(document: dict) -> dict:
    """An empty card of the same shape, for the ``+`` button to clone."""
    return _card({}, parameter_keys(document), _legend(document), _multiple_keys(document))


def merge_searches(searches: list[dict], path: str | Path | None = None) -> str:
    """The document as text, with ``searches`` replaced and everything else kept.

    ``_legend`` - and any other top-level key someone adds by hand - survives a save.
    The form only knows about search entries, so writing the whole document from it
    would delete the schema it was built from.
    """
    document = load_document(path, missing_ok=True)
    document[SEARCHES_KEY] = searches
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
