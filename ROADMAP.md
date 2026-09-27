# Roadmap

What's being added to `job-seeker-ai` next.

The four-step pipeline — scrape, hard-filter, LLM enrich, review — runs end to
end today. Everything below extends it.

## Scraping

### Support for additional job sites

The scraper targets LinkedIn today. New sites are added as adapters against the
contract in [`scrapers/base.py`](src/job_seeker_ai/scrapers/base.py) — `SITE`,
`async login()`, `async run(specs, headless=True)` — plus an entry in
`REGISTRY`, which is what the CLI's `--site` flag resolves through.

- A site with its own search-parameter vocabulary gets a mapping alongside
  [`scrapers/params.py`](src/job_seeker_ai/scrapers/params.py), so `searches.json`
  keeps using the same friendly names across every source.
- The preflight in `services/scraping.py` is where a site that needs a different
  browser engine declares it, so each engine is installed once per run rather
  than once per site.
- Cross-site identity: `job_sources` is keyed on `(site, source_id)`, and
  `jobs.norm_key` is reserved for merging the same posting when it appears on
  more than one source.

## Enrichment

### Pluggable LLM backends

[`llm_clients.py`](src/job_seeker_ai/llm_clients.py) is the single module that
talks to a model. It drives LM Studio over its OpenAI-compatible endpoint today;
the next step is making the backend selectable behind the same interface.

- **Local Ollama** — a second local runtime, keeping the pipeline
  credential-free and fully offline.
- **Online API calls** — for the postings worth a hosted model, with the key read
  from the environment.
- Backend selection joins the environment overrides already in
  [`config.py`](src/job_seeker_ai/config.py) (`LLM_BASE_URL`, `LLM_API_KEY`,
  `LLM_MODEL`), so a run picks its model without code changes.

## Review site

### Edit configuration from the browser

The Markdown criteria files and `searches.json` are the pipeline's whole
configuration surface, and they are edited on disk today. This brings that into
the review site, so criteria can be tuned while looking at the postings they
produced.

- Edit and save [`criteria/must_words.md`](criteria/must_words.md),
  [`criteria/forbidden_words.md`](criteria/forbidden_words.md) and
  [`criteria/deal_breakers.md`](criteria/deal_breakers.md) in place.
- Edit [`searches.json`](searches.json) with its `_legend` values validated, so
  a typo in `work_type` or `experience_levels` is caught at edit time rather
  than during URL construction.
- Show what the current word rules would accept or reject, so a change can be
  judged against real rows before it is saved.

### Trigger pipeline steps from the browser

Buttons to run the backend steps — scrape, filter, enrich — from the review
page, with progress and outcome reported inline instead of in a terminal.

- Run each step independently, matching the CLI's separate commands.
- Stream per-job progress during a scrape and per-job verdicts during
  enrichment.
- Report a failed run with its reason, and allow a run to be stopped.
- One run at a time, since a scrape drives a browser and enrichment loads a
  model into memory.
