# Review website

Step 4 of the pipeline: a small FastAPI app for reading the scraped jobs and
triaging them. It is a **separate, optional process** — the scraper and the
enricher never import it — and it can be started, stopped or dropped at any
point without touching the data.

```bash
python -m job_seeker_ai serve                  # http://127.0.0.1:8000
python -m job_seeker_ai serve --port 9000 --reload
```

Binds to `127.0.0.1` by default. It has no authentication, and it exposes status
writes and the project's `.env` — the model API key included — so it
is deliberately **not** meant to be reachable from a network.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | list, filtered and sorted |
| GET | `/jobs/{job_id}` | full detail for one job |
| POST | `/jobs/{job_id}/invalidate` | set status `invalidated` |
| POST | `/jobs/{job_id}/reactivate` | set status `active` |
| GET | `/settings` | General: one input per `.env` key |
| POST | `/settings` | write `.env` |
| GET | `/settings/filters` | Filters: the two word lists |
| POST | `/settings/filters` | write both word lists |
| GET | `/settings/deal-breakers` | Deal Breakers: nested cards |
| POST | `/settings/deal-breakers` | write `deal_breakers.md` |
| GET | `/settings/linkedin` | Linkedin: one card per search |
| POST | `/settings/linkedin` | write the `searches` array |
| GET | `/static/...` | stylesheet and script |

An unknown `job_id` on the detail route is not a 404: it redirects (303) back
to the page named in the `back` query parameter, defaulting to `/`. That
parameter is threaded through every link and form so triage keeps its filter
and sort context.

## List page

One optional query parameter:

| Param | Meaning |
|---|---|
| `status` | `active` / `invalidated` / `rejected`; blank means no filter |

The list is always ordered by `scraped_at DESC` — newest first — and the page
states that as a fixed `order: Added ↓` hint. There is no sort parameter and no
user-supplied SQL fragment anywhere in the query; `min_cv`, `min_params` and
`sort` existed only to filter and order by score columns that no longer exist.
Unknown query parameters are ignored rather than rejected, so an old bookmark
still works.

Each row shows the posting's own `posted` age and the `added` timestamp
separately, so the two cannot be confused.

### Deal-breaker pills

The `deal_breakers` column renders `jobs.deal_breakers_result` as a coloured
pill. The mapping is fixed on both sides — the enrichment step writes these
values (`DEAL_BREAKERS_FAILED` / `DEAL_BREAKERS_PASSED` in
`services/enrichment.py`) and this template reads them:

| `deal_breakers_result` | Pill | Meaning |
|---|---|---|
| `0` | green | `OK` — passes every deal breaker |
| `1` | red | `Fail` — fails at least one |
| `NULL` | grey | `Pending` — not evaluated yet |

Each pill carries a `title` tooltip, because colour alone is not a label.

Anything unrecognised falls through to `Pending` rather than claiming a pass: a
false green is the one outcome worth avoiding. That also fixes a reporting bug —
the cell used to be `{{ job.deal_breakers_result or '' }}`, and since `0` is
falsy **a pass rendered as an empty cell**, while only a fail showed a bare `1`.

The classes live with the rest of the styling in the single hand-written
`static/css/style.css` (`.pill`, `.pill--ok`, `.pill--fail`, `.pill--pending`).
There is no CSS framework in this project and deliberately no build step.

## Statuses

| Status | Set by | Meaning |
|---|---|---|
| `active` | default | in play |
| `rejected` | the word filter | failed a hard word rule before reaching the LLM |
| `invalidated` | the enrichment step, or you | failed a deal breaker, or dismissed by hand |

Status is the *only* mutation this app performs. Nothing is ever deleted, so
why a job was dismissed is always still visible (`filter_reason`,
`deal_breakers_reason`). The enrichment step sets `invalidated` on its own — see
[pipeline.md](pipeline.md#the-end-of-pass-reconciliation) — which means
`reactivate` here can be undone by the next enrichment pass.

```
tr.invalidated, tr.rejected { opacity: 0.45; }
```

Rejected and invalidated rows stay in the list, greyed out, so you can audit
the filter rules against real postings.

## Settings page

Each section is **its own page**, reached from a sidebar on the left of every one:
`/settings` is **General**, `/settings/filters` is **Filters**,
`/settings/deal-breakers` is **Deal Breakers** and `/settings/linkedin` is
**Linkedin**, last in the list. The sidebar entry for the page you are on is
highlighted, so the four read as one area with four tabs rather than one long form.

`NAV` in `web/app.py` drives the sidebar (slug, title, href) and `GENERAL_FIELDS`
drives the inputs, so adding a variable is a one-line edit rather than template
surgery. Each page has its own form and Save button, so saving one never rewrites
what the other owns.

General renders **one input per managed `.env` key**, so the form is a direct view
of the file rather than free text:

| Keys | Input |
|---|---|
| `STATE_PATH`, `SEARCHES_PATH`, `CRITERIA_DIR`, `JOBS_DB_PATH` | text |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_MAX_CONCURRENCY` | text |
| `LLM_API_KEY` | `type="password"` — the value reads as dots |

The page carries no help text: the sidebar, the field labels and a **Save** button
are the whole UI. `LI_EMAIL` and `LI_PASSWORD` are gone — `login` is a hand sign-in,
and nothing reads credentials from the environment any more.

Behaviour worth knowing:

- **Values come from the file, not from the running process**, so the form shows
  what the next start will read even when that differs from what is loaded now.
- **Only the managed keys are written back.** A key the page does not know about is
  carried over unchanged, so a line added by hand is never silently dropped.
- **The path is fixed** (`config.ENV_PATH`) and never read from the request, and
  only managed keys are read from the body — a form field can neither introduce a
  variable nor aim the write at another file.
- **It is written atomically**, to a sibling temp file that is then moved into
  place, so a crash mid-save cannot truncate the file. `mkstemp` also means it
  lands as `0600` instead of inheriting a permissive umask.
- **Nothing takes effect until a restart.** `config.py` resolves the environment
  once at import, so the running `serve` — and every other step — keeps the values
  it started with.
- **Comments do not survive a save.** The file is rewritten as one `KEY=value` line
  per managed key; `.env.example` stays the place where the explanation lives.
- **The API key is masked on screen but still carried in the input's `value`**, so
  saving without retyping keeps it. That is a trade-off the loopback-only,
  unauthenticated binding is what makes acceptable.

The body is parsed with `urllib.parse.parse_qs` rather than FastAPI's `Form`, which
would pull in `python-multipart` for the sake of one form.

### Filters

The second section is a two-column editor over the word lists the filter step reads:
`criteria/must_words.md` on the left, `criteria/forbidden_words.md` on the right, an
even split from `grid-template-columns: 1fr 1fr`.

- **One editable row per line.** Both files are plain rule lists with no comments,
  so reading lines in and writing lines out is lossless.
- **`+` in the column header appends a blank row**, and the trash button removes the
  row it belongs to. Both are client-side, in `static/js/settings.js` — the only
  JavaScript in the project, and dependency-free.
- **Save overwrites both files wholesale** from the rows on the page, in order. A
  blank row is dropped rather than written as an empty line, and emptying a column
  empties the file, which the filter step reads as "no rules".
- **Both files go through the same atomic writer as `.env`** — temp file plus
  `os.replace`, so a crash mid-save cannot leave a half-written rule list.

Each row input carries its column's name (`must_words` / `forbidden_words`), so the
form posts one list per column and the server needs no index fields. That is also
why the script only has to clone and remove DOM nodes: order is document order.

### Deal Breakers

The third section edits `criteria/deal_breakers.md`, one **card per rule**, and is
the only one with nested lists:

| Row | Fields |
|---|---|
| core (mandatory) | `key` and `description` |
| Evaluation Principles (optional) | `title` and `principle`, one row each |
| Examples (optional) | a `score` select and `example`, one row each |

`+` adds a card, and each card has its own `+` on both sub-lists; every row has a
trash button. All of it runs through the same `static/js/settings.js` as Filters —
one delegated handler, driven by `data-group` / `data-rows` / `data-row`, so a card
and a sub-item inside it are removable by the same code.

**The file stays flat.** `parse_deal_breakers` and `format_deal_breakers` in
`criteria_loaders.py` are the only code that understands the mapping, so the page
and the LLM prompt cannot drift apart:

```
- (key): (description)
...

--- GENERAL EVALUATION PRINCIPLES ---
- (key): (title) - (principle)
...

--- EXAMPLES OF CORRECT SCORING ---
- (key): (score) - (example)
```

The **key is the join**: a sub-item belongs to the deal breaker whose key it
repeats, which is why keys have to be unique within a file. Notes on the edges:

- A sub-item's text splits on the **first** ` - ` only, so an example like
  `60,000 - 75,000` keeps its own dash.
- A sub-item with no matching parent becomes a card with an empty description
  rather than being dropped.
- A card with **no key** is skipped on save — it could not own sub-items on the way
  back in.
- Only lines starting with `- ` are data. Any other line is ignored on load and
  therefore gone on the next save, so hand-edit the file in this form.

`saving the loaded state changes nothing` is a test, and it runs against the real
shipped file as well as a fixture.

### Linkedin

The fourth section edits `job_sites/linkedin.json`, and it is the only one whose
form is **generated from the file itself**: `_legend` lists the allowed values for
the constrained search parameters, so it doubles as the schema.

- **One card per entry in `searches`**, holding `name`, `pages` and one control per
  parameter.
- **A parameter the legend lists becomes a `<select>`** of exactly those values;
  everything else — `keywords`, `location`, and any parameter added by hand — is
  free text. The field list therefore starts from the parameters already in the
  data and only then adds legend keys no search uses yet: a legend-only list would
  hide the two that matter most.
- **`_legend._multiple` names the parameters that take several values**, and those
  get a **row of checkboxes**: `work_type`, `employment_type` and
  `experience_levels`, since LinkedIn's `f_WT`, `f_JT` and `f_E` all accept a
  comma-separated list and `scrapers/params.py` joins one. Not a `<select multiple>`
  — that needs Ctrl-click and reads as a single choice. `posted_within` is
  deliberately absent, because one time bucket is one bucket, and stays a dropdown.
  The flag is read from the legend rather than inferred from the stored value, so a
  search that has never set `experience_levels` still gets the checkboxes instead of
  locking to the first choice made in it.
- **`_legend` itself never reaches the form.** Keys starting with `_` are
  documentation or schema — `_comment` explains the file inside the file — and a
  save *merges* into the existing document, so the legend and any other top-level
  key survive.
- **`pages` is coerced to a positive integer**, because a string there would break
  the adapter's page loop; an unparseable entry falls back to 1, the same default
  the loader uses for a missing key.
- **A card with no name is dropped** on save, like an empty row elsewhere.

Saving rewrites `searches` in document order. `merge_searches` re-serialises the
whole document, so a hand-formatted file comes back fully indented — data
preserved, whitespace not.

## Structure

One module, `src/job_seeker_ai/web/app.py`, twelve routes, plus:

- `templates/base.html` — shared `<head>`, stylesheet link and a `scripts` block
- `templates/settings_layout.html` — the settings sidebar and the content card,
  with a `section` block each page fills in
- `templates/settings_general.html`, `templates/settings_linkedin.html`,
  `templates/settings_filters.html`, `templates/settings_deal_breakers.html` — one
  per settings page
- `templates/jobs.html`, `templates/job_detail.html` — `{% extends %}` the base
- `static/css/style.css` — one stylesheet, previously inlined twice in both
  templates
- `static/js/settings.js` — the only script: add and remove rows in Filters and
  Deal Breakers. It is listed in `package-data` in `pyproject.toml`, so an
  installed wheel ships it

All SQL lives in `repositories.py`. The route functions only translate HTTP
into repository calls and hand the results to a template.

## Current limitations

- Each request opens its own SQLite connection rather than using a dependency
  and a pool. Fine for a single local user; it would be the first thing to
  change if this ever served more than one.
- `src/tests/test_web_routes.py` covers the list page, ordering, filters, detail
  page, static asset, both status mutations and the settings form. Those tests
  skip themselves if the `web` extra is not installed (`pip install -e ".[dev]"`
  installs it), and they point `config.ENV_PATH` and both word lists at temp files so
  a run can never write the real ones.
- A `jobs.db` created before the schema was slimmed still carries six unused
  columns. Nothing reads them and serving works fine, but they only disappear
  from a freshly created database.
