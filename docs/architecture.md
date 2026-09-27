# Architecture

## Layout

```
job_seeker_ai/
├── criteria/                  user-edited rules (versioned, not code)
│   ├── must_words.md
│   ├── forbidden_words.md
│   └── deal_breakers.md
├── searches.json              user-edited search definitions
├── docs/
├── src/
│   ├── job_seeker_ai/
│   │   ├── config.py          every path + env override, in one place
│   │   ├── entities.py        SearchSpec, ScrapedJob
│   │   ├── exceptions.py      JobSeekerError, ScrapeError, …
│   │   ├── browsers.py        browser-engine install preflight
│   │   ├── storages.py        schema + connection factory
│   │   ├── repositories.py    every SQL statement
│   │   ├── parsers.py         salary / interview / dedupe-key extraction
│   │   ├── criteria_loaders.py  reads criteria/*.md
│   │   ├── filters.py         pure word-filter verdicts
│   │   ├── llm_clients.py     LM Studio lifecycle + OpenAI client
│   │   ├── evaluators.py      deal-breaker prompt + verdict models
│   │   ├── cli.py             argument parsing and dispatch
│   │   ├── __main__.py        `python -m job_seeker_ai`
│   │   ├── scrapers/
│   │   │   ├── base.py        adapter contract + registry
│   │   │   ├── params.py      LinkedIn facet codes
│   │   │   └── linkedin.py    the Camoufox scraper
│   │   ├── services/
│   │   │   ├── scraping.py    searches.json -> adapter
│   │   │   ├── filtering.py   word filter over the database
│   │   │   └── enrichment.py  LLM pass over pending jobs
│   │   └── web/
│   │       ├── app.py         FastAPI app (one module, four routes)
│   │       ├── templates/
│   │       └── static/css/
│   └── tests/
├── pyproject.toml
└── README.md
```

## Why `src/`

The project root holds things that are *configured* (criteria, searches,
docs, packaging) and `src/` holds the things that are *executed*. Without
that split, `scripts/` and `web/` sat next to `criteria/` and the packaging
files, and neither could be imported as a package: the old `web/app.py`
needed `sys.path.insert(...)` plus a bare `import common`, and
`scripts/agent.py` computed its criteria directory relative to itself, which
pointed at a directory that does not exist.

Everything is now reachable as `job_seeker_ai.<module>`, with no path
manipulation anywhere.

## Layer direction

```
        cli.py          web/app.py
            \              /
             v            v
            services/            <- decides *when*
             |   |   |
             v   v   v
   repositories/  scrapers/  evaluators/     <- does *what*, one concern each
        |              |            |
        v              v            v
   storages.py    parsers.py    llm_clients.py
        |
        v
     config.py       <- every path and environment lookup
```

Rules that hold today, and are worth keeping:

- **`services/` never imports a web framework.** `web/` depends on
  `services/`, never the reverse, so the pipeline can be scripted or tested
  without FastAPI installed.
- **Only `repositories.py` writes SQL.** A route change cannot quietly alter
  a scrape-time guarantee.
- **`parsers.py` and `filters.py` do no I/O.** They take strings and return
  values, which is why they have the cheapest tests.
- **Heavy imports are lazy.** `camoufox` is imported only by the LinkedIn
  adapter, and `uvicorn` only by `serve`, so `--help` and the word filter do
  not need a browser stack.

## Two deliberate layering compromises

Both are noted rather than hidden:

1. **`repositories.py` has sections.** It is one module covering scrape-time,
   filter-time, enrich-time and review-time queries. That is a single
   1-function-per-query module of ~200 lines; splitting it into four modules
   would add imports without adding clarity. If it grows past that, split by
   the section comments.
2. **`web/app.py` is a single module.** Four endpoints do not justify a router
   package. The SQL separation still applies: the one ordering the list uses
   lives in `repositories.list_jobs`.

## Site adapters

An adapter is a *module* satisfying `scrapers/base.py`:

```
SITE: str
async login() -> None
async run(specs: list[SearchSpec], headless: bool = True) -> None
```

Modules rather than classes because adapters are stateless collections of
functions — a class would add ceremony without behaviour. `base.REGISTRY`
maps a site key to a module path and imports it on demand. `linkedin.py` is
the only entry today; the earlier `common.py` docstring promised this
extension point but nothing implemented it.

**Adapters carry no environment setup.** The scraping phase calls
`services.scraping.prepare()` once per command, before it reaches any adapter,
and that is where a browser or driver is made present (`browsers.py`). Two
consequences for a new site:

- it does not implement, or even know about, an install check;
- the check cannot be repeated per site or per launch.

`src/tests/test_browser_install.py` enforces this at source level: the preflight
must be called exactly once in `services/scraping.py` and never from anything
under `scrapers/`. Adding a site that needs a different engine means adding one
call to `prepare()`, not touching the adapter contract.

## Configuration

`config.py` is the only module that reads the environment:

| Variable | Default | Purpose |
|---|---|---|
| `STATE_PATH` | `./state.json` | persisted browser session (**secret**) |
| `SEARCHES_PATH` | `./searches.json` | search definitions |
| `CRITERIA_DIR` | `./criteria` | rule files |
| `JOBS_DB_PATH` | `./jobs.db` | the database; the documented test hook |
| `LLM_BASE_URL` | `http://localhost:1234/v1` | LM Studio endpoint |
| `LLM_API_KEY` | `lm-studio` | ignored by LM Studio, required by the SDK |
| `LLM_MODEL` | `qwen3.8-27b` | local LM Studio model id |
| `LI_EMAIL` / `LI_PASSWORD` | unset | optional login pre-fill |

Paths resolve **once, at import**. Tests therefore set `JOBS_DB_PATH` before
importing anything from the package — see `src/tests/conftest.py`.

## Database

One SQLite file, three tables: `jobs`, `job_sources`, `meta`. The schema is
reapplied on every connection via `CREATE TABLE IF NOT EXISTS`, so there is no
migration step and no migration tool. WAL mode plus a 30 s busy timeout is what
allows the concurrent page workers to share the file; writes are additionally
serialised through an `asyncio.Lock` in the scraper.

The schema is deliberately lean — it holds only what the four steps write
today. Two indexes: `norm_key` (dedupe scaffolding) and `scraped_at`, which is
the single ordering the review list uses.

Schema notes:

- `extra_fields` is an unused JSON catch-all, kept as a deliberate escape hatch.
- `CREATE TABLE IF NOT EXISTS` cannot drop columns, so a database created before
  the scoring columns were removed still carries them. Nothing reads them.
- The reference `jobs.db` also contains a `sqlean_define` table created by the
  SQLite `sqlean` extension. Nothing in this codebase uses it.

What's being added next is in [ROADMAP.md](../ROADMAP.md).
