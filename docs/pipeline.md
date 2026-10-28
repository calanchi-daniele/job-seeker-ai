# The pipeline

Four independent steps. Each one reads and writes the same SQLite file and
can be run on its own; nothing is scheduled or daemonised.

```
linkedin.json ──┐
                ├─> [1] scrape ──> jobs.db ──> [2] filter ──> jobs.db
criteria/*.md ──┘                     │
                                      └─> [3] enrich ──> jobs.db ──> [4] review
```

## 0. `login` — once, interactively

```bash
python -m job_seeker_ai login
```

Opens a headful browser, lets you sign in by hand (including any 2FA or
checkpoint), then writes the session to `state.json`.

This and `run` are both scraping-phase commands, and `services.scraping.prepare()`
runs the browser preflight **once** for each before any site is opened: if the
Camoufox build is missing it runs `camoufox fetch`, then proceeds. It is a
filesystem probe when the browser is already installed (about 25 ms, no
network), and it lives at the phase boundary rather than in the adapters — so a
second site inherits it instead of adding another check.

`state.json` is **live session cookies**. It is listed in `.gitignore` and
must never be committed, attached or pasted anywhere. Delete it to force a
fresh sign-in.

> **Note.** `run` also calls this same interactive login at startup, so `login` is
> not really a separate once-only step — and a scrape cannot currently be
> scheduled unattended, because it blocks on stdin.

## 1. `run` — scrape

```bash
python -m job_seeker_ai run              # headless
python -m job_seeker_ai run --headful    # watch the browser work
```

- Signs in interactively first (**see item 8 above** — this is not yet optional).
- Reads `job_sites/linkedin.json`: a list of named searches with `pages` and `params`.
- Translates friendly param names into LinkedIn facet codes
  (`scrapers/params.py`).
- Opens search-result pages concurrently (`CONCURRENT_SEARCH_PAGES = 2`),
  scrolls to collect job ids, then visits each job page in its own browser
  context under a hard 60 s timeout. A search page that fails is retried once
  in a fresh context before the run gives up on it.
- Skips the page fetch for any id already on record: every recorded
  `source_id` for the site is loaded once into an in-memory set at the start of
  the run, so a re-run costs almost nothing. That set is a filter, not a lock —
  the authoritative check is the one inside `upsert_job`, which is what stops a
  second process double-inserting.
- Persists one row per posting, plus its source record.
- Records `last_run_at` in `meta`, which the next run uses to widen the
  "posted within" window so a long pause cannot silently skip postings.

Deduplication is intentionally two-level:

| Level | Key | Behaviour |
|---|---|---|
| exact posting | `(site, source_id)` primary key | same posting seen again is a no-op |
| cross-search | `norm_key` on title+company+location | recorded, but **not** used to merge |

So the same role found by two overlapping searches becomes two rows. That is
deliberate: the observed duplicate problem was always the exact same posting,
and merging near-duplicates needs a fuzzy pass that does not exist yet. The
`norm_key` column is there for when it does.

## 2. `filter` — hard word rules, no LLM

```bash
python -m job_seeker_ai filter
python -m job_seeker_ai filter --dry-run
```

Purely deterministic string matching, run before enrichment so the model is
never pointed at postings that cannot pass.

- `criteria/must_words.md` — one rule per line, `word OR word`. A job must
  match at least one word on **every** line (AND across lines, OR within one).
- `criteria/forbidden_words.md` — one phrase per line. Any match rejects.

Matching is case-insensitive substring, not word-boundary regex, because the
rule vocabulary includes `C#`, `.NET` and `forward deployed`.

Results are written as a status change plus `filter_reason` — never a delete,
so the review site can still show why something was dismissed. Already-filtered
jobs are not re-evaluated (`filtered_at`), so `--dry-run` is safe to repeat.

## 3. `enrich` — model API

```bash
python -m job_seeker_ai enrich
```

One command does a whole pass:

1. check that `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` are set, and stop
   before spending anything if they are not
2. evaluate every pending active job
3. invalidate every active job that failed a deal breaker (see below)

Nothing is loaded, served or supervised here: the endpoint is external and stays
up between runs, so an interrupted pass leaves nothing running behind it.

Pending means `enriched_at IS NULL AND status = 'active'` — so the filter step
reduces what the model has to read, and invalidating a job in the review site
keeps it out of future passes.

Each job is sent one system message (the deal-breakers file) plus one user
message (the posting). The reply is parsed into a typed
`DealBreakersEvaluation` rather than free text, so it stores directly. The
prompt demands verbatim quotes: a hallucinated verdict is easier to catch when
the model has to cite the posting.

### Evaluating in parallel

The evaluations can be fanned out concurrently - `LLM_MAX_CONCURRENCY` of them at
a time, 1 by default. The whole pending set is queued at once and a semaphore
decides how much runs together, so a slow posting does not hold up the ones
behind it.

- **Database writes are serialised** with a lock. The concurrency is confined to
  the model calls; one writer at a time keeps SQLite out of the picture
  entirely, at a cost measured in microseconds against multi-second inference.
- **The default of 1 is the polite setting, not the fast one.** It keeps requests
  strictly sequential, which is what a rate-limited key wants; raise it when the
  endpoint tolerates parallel requests.
- One failed evaluation never blocks or discards the others: it is reported, left
  pending and retried on the next pass, while its siblings are still saved.

### The model API

Step 3 is a plain OpenAI-compatible client. `llm_clients.py` holds a single
`AsyncOpenAI` pointed at `LLM_BASE_URL`, and nothing in this project loads a
model, holds weights, or starts, watches or stops a process: the endpoint is yours
to run.

Three settings are required, and there is deliberately no default endpoint -
aiming the pipeline at a provider has to be something you asked for by name, so a
missing value is reported before the first request instead of quietly choosing an
API for you. `llm_clients.check_config()` enforces that, called from
`enrichment.enrich_jobs()` at the top of every pass.

`llm_clients.complete_structured()` is the only place a request leaves the
project, and it normalises the one difference that matters:

- **A rejected schema falls back to plain JSON.** The first attempt asks for a
  strict `json_schema`; a host that cannot honour it is retried once with
  `json_object`, which specifies no fields of its own, so that attempt also gets
  an example of the desired shape in the prompt - DeepSeek's documentation spells
  the requirement out, and other hosts behave the same way. The reply is still
  validated here rather than failing the job.

Everything else on the wire is standard OpenAI - model, messages, temperature and
`max_tokens` - with no provider-specific `extra_body`.

### The end-of-pass reconciliation

Once the verdicts are saved, the pass runs one bulk update: every row with
`deal_breakers_result = 1` **and** `status = 'active'` becomes `invalidated`, so
the model's verdict takes effect in the same command instead of waiting for you
to triage by hand. After a clean run, no `active` job fails a deal breaker.

Three consequences worth knowing:

- **It covers the whole table, not just this pass's jobs.** That is deliberate:
  it also heals a verdict a previous run wrote but never acted on (a crash
  between the two statements). It therefore runs even when nothing was pending.
- **It has no memory of `reactivate`.** A job you put back to `active` in the
  review site, which still fails a deal breaker, is invalidated again on the
  next pass. If manual decisions should win, narrow the predicate in
  `repositories.invalidate_failing_deal_breakers`.
- **`rejected` rows are untouched.** Only `active` ones move, so a job the word
  filter already ruled out keeps a status that says why.

The count is reported at the end of the run, next to the enrichment summary.

If a call fails or returns no usable verdict, the job is left **completely
untouched** and stays pending for the next pass, rather than being marked
enriched with an empty result. Each run ends with a summary:

```
12 enriched, 1 left pending out of 13
```

`deal_breakers_result` is written as **1 when the posting fails** at least one
deal breaker and **0 when it passes** (`DEAL_BREAKERS_FAILED` /
`DEAL_BREAKERS_PASSED`). The review site's pills read exactly those values, so
the two mappings have to stay in step.

> **Only deal breakers are evaluated.** The culture-scoring criteria file and the
> columns it would have fed were removed to keep the project lean.

## 4. `serve` — review

```bash
python -m job_seeker_ai serve                  # http://127.0.0.1:8000
python -m job_seeker_ai serve --port 9000 --reload
```

An optional, separate process. See [review-website.md](review-website.md).

## Running the whole thing

```bash
./run_all.sh            # requires bash (Git Bash/WSL on Windows)
./run_all.sh --headful
```

Equivalent on PowerShell:

```powershell
.\.venv\Scripts\python.exe -m job_seeker_ai run
.\.venv\Scripts\python.exe -m job_seeker_ai filter
.\.venv\Scripts\python.exe -m job_seeker_ai enrich
```

## Environment overrides

All optional; see `.env.example` and [architecture.md](architecture.md#configuration).
The one that matters for tests is `JOBS_DB_PATH`, which points the whole
pipeline at a throwaway database.
