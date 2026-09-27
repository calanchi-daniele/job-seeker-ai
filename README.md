# job-seeker-ai

A local-first job-hunting pipeline that scrapes job postings, discards most of
them with blunt word rules, and lets a local LLM do the rejecting you'd rather
not do yourself. No cloud keys required: your criteria live in Markdown and your
data never leaves the machine.

```
searches.json ──┐
                ├─> [1] scrape ──> jobs.db ──> [2] filter ──> jobs.db
criteria/*.md ──┘                     │
                                      └─> [3] enrich ──> jobs.db ──> [4] review
```

Four steps, each runnable on its own, all reading and writing one SQLite file.
Nothing is scheduled or daemonised; the review site is a separate optional
process.

## Requirements

- Python **3.10+** (developed and tested on 3.12)
- [LM Studio](https://lmstudio.ai) with the `lms` CLI on `PATH` — step 3 only
- A LinkedIn account — step 1 only

The Camoufox **browser** is a separate download from the Python package. The
scraping phase checks for it once per command, before opening any site, and runs
`camoufox fetch` automatically when it is missing — so there is no manual setup
step; the first `login` or `run` may just pause while it downloads.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"              # POSIX
.\.venv\Scripts\pip.exe install -e ".[dev]"    # Windows PowerShell
```

`.[dev]` adds the web extra and the test tooling. Use `.[web]` for just the
review site, or a bare `-e .` for the headless pipeline.

## Use

| Step | Command | What it needs |
|---|---|---|
| 0 | `login` | a browser; interactive, once |
| 1 | `run [--headful]` | `state.json` from step 0 |
| 2 | `filter [--dry-run]` | nothing — no LLM, no browser |
| 3 | `enrich` | LM Studio running locally |
| 4 | `serve [--port 8000]` | nothing |

Commands are invoked as `python -m job_seeker_ai <command>`, or via the
installed `job-seeker-ai` entry point. On Windows, prefix the module form with
`.\.venv\Scripts\python.exe`. `./run_all.sh` runs steps 1–3 in order and needs
bash (Git Bash/WSL on Windows).

`enrich` finishes by invalidating every active job that failed a deal breaker, so
a run leaves no failing job still marked as in play.

> **Note:** `run` also signs in interactively at startup, so a scrape cannot be
> scheduled unattended yet.

## Configuration

Everything the tool needs to know about *you* is a plain file you edit by hand:

| File | What it is |
|---|---|
| [`searches.json`](searches.json) | `{_legend, searches: [{name, pages, params}]}`. `keywords` and `location` are free text; `work_type`, `posted_within`, `employment_type` and `experience_levels` must use the exact values listed in the file's own `_legend`. |
| [`criteria/must_words.md`](criteria/must_words.md) | One rule per line, `word OR word`. A job is rejected unless it matches at least one word on **every** line. |
| [`criteria/forbidden_words.md`](criteria/forbidden_words.md) | One phrase per line. A job is rejected if any of them appears. |
| [`criteria/deal_breakers.md`](criteria/deal_breakers.md) | Strict pass/fail rules — remote-from-Italy availability, minimum salary. Sent to the local LLM as the prompt's criteria. |
| [`.env.example`](.env.example) | Optional overrides: `LI_EMAIL`, `LI_PASSWORD`, `JOBS_DB_PATH`, `CRITERIA_DIR`, `STATE_PATH`, `LLM_MODEL`, … Copy to `.env`. |

The two word lists are applied by cheap string matching with no model involved;
only `deal_breakers.md` reaches the LLM.

`state.json` holds **live LinkedIn session cookies**. It is gitignored. Never
commit it, attach it to an issue, or paste it into a screenshot.

## Tests

```bash
pytest
```

`src/tests/conftest.py` points the whole pipeline at a throwaway SQLite file in
the system temp directory via `JOBS_DB_PATH`, so the suite never touches your
real `jobs.db`. No model, no browser and no network are needed — the LLM and the
scraper are stubbed. The web tests skip themselves when the `web` extra is not
installed.


## Architecture

```
src/job_seeker_ai/
├── cli.py  __main__.py     entry points: `python -m job_seeker_ai`
├── config.py               every path, resolved once, with env overrides
├── entities.py             SearchSpec, ScrapedJob
├── exceptions.py           JobSeekerError and its subclasses
├── browsers.py             browser-engine install preflight
├── storages.py             schema + connection factory
├── repositories.py         every SQL statement
├── parsers.py              salary extraction + dedupe key
├── criteria_loaders.py     reads criteria/*.md
├── filters.py              pure word-filter verdicts
├── llm_clients.py          LM Studio lifecycle, OpenAI-compatible client
├── evaluators.py           deal-breaker prompt and typed verdicts
├── scrapers/               base.py (contract + registry) · params.py · linkedin.py
├── services/               scraping.py · filtering.py · enrichment.py
└── web/                    app.py · templates/ · static/
```

`cli`/`web` → `services` → `repositories`/`scrapers`/`evaluators` → `config`.
`services/` imports no web framework, only `repositories.py` writes SQL,
`parsers.py` and `filters.py` do no I/O, and adapters carry no environment setup
— that belongs to the scraping phase. The reasoning, including the two layering
compromises made on purpose, is in [docs/architecture.md](docs/architecture.md).

## Documentation

| Document | What's in it |
|---|---|
| [docs/pipeline.md](docs/pipeline.md) | Each step in detail: what it reads, what it writes, how deduplication works |
| [docs/review-website.md](docs/review-website.md) | Routes, filters, the status model, the deal-breaker pills |
| [docs/architecture.md](docs/architecture.md) | Module map, layering rules, configuration, database schema |
| [ROADMAP.md](ROADMAP.md) | What's being added next: more scraping sites, pluggable LLM backends, and driving the pipeline from the browser |
| [`criteria/`](criteria) | The Markdown rules the pipeline is driven by |
| [LICENSE](LICENSE) | MIT |

## Caveats

- **Scraping LinkedIn is against their Terms of Service.** This is a personal
  tool used on the author's own account at low volume; the code makes no attempt
  to be polite on your behalf beyond a delay between job pages and a 60-second cap
  per page. Use it on your own account, at your own risk.
- The scraper is written against LinkedIn's markup, which is not a stable API.
  Nothing here can detect a markup change for you — the extractors return `None`
  for fields that no longer match, so a broken selector shows up as missing data
  rather than an error.
- The review site is unauthenticated and binds to `127.0.0.1`. It exposes status
  writes, so it is not meant to be reachable over a network.
- The criteria are personal. `must_words.md` and `deal_breakers.md` in particular
  encode one specific person's requirements — treat them as examples of the
  format, not as sensible defaults.
