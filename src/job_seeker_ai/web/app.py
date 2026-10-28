"""Review website: browse and triage scraped jobs, and edit the project's settings.

The mutations are a job's status, the ``.env`` file, the criteria files and the
site's searches. This is a separate optional process
(``python -m job_seeker_ai serve``) and is deliberately not wired into the scrape or
enrich runs - it can be served, restarted or dropped at any time without touching the
pipeline.

Kept as a single module on purpose: twelve endpoints do not need a router package.
All SQL lives in ``repositories``; this module only translates HTTP into repository
calls, file reads and template context.
"""

import contextlib
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode

from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_seeker_ai import config, criteria_loaders, job_sites, repositories
from job_seeker_ai.storages import connect

app = FastAPI(title="Job review")
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

# The sidebar, in page order: (slug, title, href). Each settings section is its own
# page, so the entry doubles as the active marker.
NAV = (
    ("general", "General", "/settings"),
    ("filters", "Filters", "/settings/filters"),
    ("deal-breakers", "Deal Breakers", "/settings/deal-breakers"),
    ("linkedin", "Linkedin", "/settings/linkedin"),
)

# General renders one input per key here, so adding a variable is an edit to this
# tuple and nothing else. Order is the order of the form.
GENERAL_FIELDS = (
    "STATE_PATH",
    "SEARCHES_PATH",
    "CRITERIA_DIR",
    "JOBS_DB_PATH",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MAX_CONCURRENCY",
)

# Rendered as a password input, so the value is not on screen.
SECRET_KEYS = frozenset({"LLM_API_KEY"})


@app.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: str, back: str = "/"):
    con = connect()
    job = repositories.get_job(con, job_id)
    if job is None:
        con.close()
        return RedirectResponse(back, status_code=303)
    sources = repositories.get_job_sources(con, job_id)
    con.close()
    return templates.TemplateResponse(request, "job_detail.html", {"job": job, "sources": sources, "back": back})


@app.get("/")
def job_list(request: Request, status: str | None = None):
    """The list, newest first. ``status`` is the only filter: the score columns
    that used to be filterable and sortable are not in the schema any more."""
    con = connect()
    jobs = repositories.list_jobs(con, status=status)
    sources = repositories.list_sources_grouped(con)
    con.close()

    query = {"status": status} if status is not None else {}
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "sources": sources,
            "filters": {"status": status or ""},
            "querystring": urlencode(query),
        },
    )


def _set_status(job_id: str, status: str, back: str | None):
    con = connect()
    repositories.set_job_status(con, job_id, status)
    con.close()
    return RedirectResponse(back or "/", status_code=303)


@app.post("/jobs/{job_id}/invalidate")
def invalidate(job_id: str, back: str = "/"):
    return _set_status(job_id, "invalidated", back)


@app.post("/jobs/{job_id}/reactivate")
def reactivate(job_id: str, back: str = "/"):
    return _set_status(job_id, "active", back)


# --- writing files -------------------------------------------------------


def _write_atomically(target: Path, text: str):
    """Replace ``target`` atomically, owner-readable only.

    A sibling temp file moved into place, so a crash mid-write cannot leave a
    truncated file behind - the failure mode that would cost the user their keys or
    their whole word list. ``mkstemp`` also means the file lands as 0600 instead of
    inheriting a permissive umask.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _text_for_rule_lines(lines: list[str]) -> str:
    """A rule file's contents for ``lines``, minus blanks, with a final newline.

    The word filter strips each line and skips empties
    (``criteria_loaders.load_rule_lines``), so writing anything else would round-trip
    a row the user cannot see back into the file.
    """
    kept = [line.strip() for line in lines if line.strip()]
    return "\n".join(kept) + "\n" if kept else ""


def _nav(active: str):
    """Sidebar entries, with ``active`` marked for the stylesheet."""
    return [
        {"slug": slug, "title": title, "href": href, "active": slug == active} for slug, title, href in NAV
    ]


# --- settings: general ---------------------------------------------------


def _read_settings() -> dict[str, str]:
    """The .env file as a mapping; empty when there is no file yet.

    Interpolation is off, so a ``$OTHER`` inside a value is read literally - a round
    trip through the form must not silently rewrite what was written.
    """
    if not config.ENV_PATH.exists():
        return {}
    return {key: value or "" for key, value in dotenv_values(config.ENV_PATH, interpolate=False).items()}


def _write_settings(values: dict[str, str]):
    """Write the managed keys, carrying over any other key the file already had.

    The page shows only the variables this project defines, so dropping the rest on
    save would quietly destroy a line someone added by hand.
    """
    managed = set(GENERAL_FIELDS)
    carried = [(key, value) for key, value in _read_settings().items() if key not in managed]
    lines = [f"{key}={values.get(key, '')}" for key in GENERAL_FIELDS]
    lines += [f"{key}={value}" for key, value in carried]
    _write_atomically(config.ENV_PATH, "\n".join(lines) + "\n")


@app.get("/settings")
def general_page(request: Request, saved: bool = False):
    """One input per managed .env key. ``saved`` drives the post-redirect marker."""
    values = _read_settings()
    return templates.TemplateResponse(
        request,
        "settings_general.html",
        {
            "nav": _nav("general"),
            "fields": [
                {"key": key, "value": values.get(key, ""), "secret": key in SECRET_KEYS} for key in GENERAL_FIELDS
            ],
            "saved": saved,
        },
    )


@app.post("/settings")
async def save_settings(request: Request):
    """Write the managed keys back to .env.

    Only the keys this page renders are read from the body, so a form field cannot
    introduce a variable, and the path is fixed at ``config.ENV_PATH`` rather than
    taken from the request. The redirect keeps a refresh from posting twice.
    """
    submitted = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    values = {key: submitted.get(key, [""])[0] for key in GENERAL_FIELDS}
    _write_settings(values)
    return RedirectResponse("/settings?saved=1", status_code=303)


# --- settings: filters ---------------------------------------------------


def _filter_lists():
    """The two word lists, in the order the page shows them.

    Resolved on every call rather than captured at import, so the paths stay
    ``config``'s and tests can point them at a temp directory.
    """
    return (
        ("must_words", "Must Contain", config.MUST_WORDS_PATH),
        ("forbidden_words", "Forbidden", config.FORBIDDEN_WORDS_PATH),
    )


def _read_rule_lines(path: Path) -> list[str]:
    """One editable line per rule, blank lines dropped.

    The same shape the filter step reads, so the page cannot show a row that the
    pipeline ignores.
    """
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@app.get("/settings/filters")
def filters_page(request: Request, saved: bool = False):
    """A two-column list editor over the two word files."""
    filters = [
        {"field": field, "title": title, "lines": _read_rule_lines(path)}
        for field, title, path in _filter_lists()
    ]
    return templates.TemplateResponse(
        request,
        "settings_filters.html",
        {"nav": _nav("filters"), "filters": filters, "saved": saved},
    )


@app.post("/settings/filters")
async def save_filters(request: Request):
    """Overwrite both word lists with the lines the form submitted.

    Every row input shares its column's name, so ``parse_qs`` hands back one list
    per column in document order and the two files are written wholesale. Blank rows
    are dropped: an empty line is not a rule.
    """
    submitted = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    for field, _title, path in _filter_lists():
        _write_atomically(path, _text_for_rule_lines(submitted.get(field, [])))
    return RedirectResponse("/settings/filters?saved=1", status_code=303)


# --- settings: deal breakers ---------------------------------------------


def _parse_breaker_form(body: str) -> list[dict]:
    """The posted fields, rebuilt into nested deal breakers.

    ``parse_qsl`` rather than ``parse_qs``: the nesting is carried by the *order* of
    the fields, not by their names. A ``key`` opens a deal breaker, and every
    sub-item field after it belongs to that one until the next ``key`` - which is
    also why the inputs keep plain names and the page needs no index bookkeeping.
    """
    items: list[dict] = []
    current: dict | None = None
    principle: dict | None = None
    example: dict | None = None

    for name, value in parse_qsl(body, keep_blank_values=True):
        if name == "key":
            current = {"key": value, "description": "", "principles": [], "examples": []}
            items.append(current)
            principle = example = None
            continue
        if current is None:
            # A sub-item before any key has no parent to join; drop it.
            continue

        if name == "description":
            current["description"] = value
        elif name == "principle_title":
            principle = {"title": value, "principle": ""}
            current["principles"].append(principle)
        elif name == "principle_text" and principle is not None:
            principle["principle"] = value
        elif name == "example_score":
            example = {"score": value, "example": ""}
            current["examples"].append(example)
        elif name == "example_text" and example is not None:
            example["example"] = value

    return items


@app.get("/settings/deal-breakers")
def deal_breakers_page(request: Request, saved: bool = False):
    """One card per deal breaker, each with its principles and examples."""
    return templates.TemplateResponse(
        request,
        "settings_deal_breakers.html",
        {
            "nav": _nav("deal-breakers"),
            "deal_breakers": criteria_loaders.load_deal_breaker_items(),
            "saved": saved,
        },
    )


@app.post("/settings/deal-breakers")
async def save_deal_breakers(request: Request):
    """Rewrite the deal-breaker file from the submitted cards, in one flat pass."""
    items = _parse_breaker_form((await request.body()).decode("utf-8"))
    _write_atomically(config.DEAL_BREAKERS_PATH, criteria_loaders.format_deal_breakers(items))
    return RedirectResponse("/settings/deal-breakers?saved=1", status_code=303)


# --- settings: linkedin searches -----------------------------------------

# Cards are named by document order rather than by index, so a param input is
# "param_<key>" and a "name" opens a new card.
PARAM_PREFIX = "param_"


def _page_count(value: str) -> int:
    """Pages to scrape, at least one.

    The form has nowhere to show an error and a string here would break the
    adapter's ``range()``, so an unparseable entry falls back to the same default
    ``load_searches`` uses when the key is missing.
    """
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _parse_search_form(body: str) -> list[dict]:
    """The posted cards, rebuilt into search entries.

    ``parse_qsl`` rather than ``parse_qs``: a ``name`` opens a card and every field
    after it belongs to that one until the next ``name``. A parameter that arrives
    more than once - a multi-select - becomes a list, which is a shape
    ``scrapers/params.py`` already accepts.
    """
    searches: list[dict] = []
    current: dict | None = None

    for field, value in parse_qsl(body, keep_blank_values=True):
        if field == "name":
            current = {"name": value.strip(), "pages": 1, "params": {}}
            searches.append(current)
            continue
        if current is None:
            continue
        if field == "pages":
            current["pages"] = _page_count(value)
        elif field.startswith(PARAM_PREFIX) and value:
            key = field[len(PARAM_PREFIX) :]
            existing = current["params"].get(key)
            if existing is None:
                current["params"][key] = value
            else:
                current["params"][key] = (existing if isinstance(existing, list) else [existing]) + [value]

    # A card the user added but never named cannot be scraped; drop it the way the
    # other sections drop an empty row.
    return [search for search in searches if search["name"]]


@app.get("/settings/linkedin")
def linkedin_page(request: Request, saved: bool = False):
    """One card per search, its fields built from the file's ``_legend``."""
    document = job_sites.load_document(missing_ok=True)
    return templates.TemplateResponse(
        request,
        "settings_linkedin.html",
        {
            "nav": _nav("linkedin"),
            "cards": job_sites.search_cards(document),
            "blank": job_sites.blank_card(document),
            "saved": saved,
        },
    )


@app.post("/settings/linkedin")
async def save_linkedin(request: Request):
    """Replace the ``searches`` array, merging so ``_legend`` survives the write."""
    items = _parse_search_form((await request.body()).decode("utf-8"))
    _write_atomically(config.SEARCHES_PATH, job_sites.merge_searches(items))
    return RedirectResponse("/settings/linkedin?saved=1", status_code=303)
