# Review website

Step 4 of the pipeline: a small FastAPI app for reading the scraped jobs and
triaging them. It is a **separate, optional process** — the scraper and the
enricher never import it — and it can be started, stopped or dropped at any
point without touching the data.

```bash
python -m job_seeker_ai serve                  # http://127.0.0.1:8000
python -m job_seeker_ai serve --port 9000 --reload
```

Binds to `127.0.0.1` by default. It has no authentication and exposes status
writes, so it is deliberately **not** meant to be reachable from a network.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | list, filtered and sorted |
| GET | `/jobs/{job_id}` | full detail for one job |
| POST | `/jobs/{job_id}/invalidate` | set status `invalidated` |
| POST | `/jobs/{job_id}/reactivate` | set status `active` |
| GET | `/static/...` | stylesheet |

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

## Structure

One module, `src/job_seeker_ai/web/app.py`, four routes, plus:

- `templates/base.html` — shared `<head>` and stylesheet link
- `templates/jobs.html`, `templates/job_detail.html` — `{% extends %}` the base
- `static/css/style.css` — one stylesheet, previously inlined twice in both
  templates

All SQL lives in `repositories.py`. The route functions only translate HTTP
into repository calls and hand the results to a template.

## Current limitations

- Each request opens its own SQLite connection rather than using a dependency
  and a pool. Fine for a single local user; it would be the first thing to
  change if this ever served more than one.
- `src/tests/test_web_routes.py` covers the list page, ordering, filters, detail
  page, static asset and both status mutations. Those tests skip themselves if
  the `web` extra is not installed (`pip install -e ".[dev]"` installs it).
- A `jobs.db` created before the schema was slimmed still carries six unused
  columns. Nothing reads them and serving works fine, but they only disappear
  from a freshly created database.
