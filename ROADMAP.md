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
  [`scrapers/params.py`](src/job_seeker_ai/scrapers/params.py), so the search
  documents keep using the same friendly names across every source.
- The preflight in `services/scraping.py` is where a site that needs a different
  browser engine declares it, so each engine is installed once per run rather
  than once per site.
- Cross-site identity: `job_sources` is keyed on `(site, source_id)`, and
  `jobs.norm_key` is reserved for merging the same posting when it appears on
  more than one source.

## Enrichment

### Cheaper, faster evaluation

[`llm_clients.py`](src/job_seeker_ai/llm_clients.py) is a single OpenAI-compatible
client, so which model answers is already a `.env` change and not a code change.
What is left sits around the call itself.

- **Retry with backoff on rate limits**, so a burst that trips a 429 continues
  instead of leaving the rest of that batch pending for the next pass.
- **Per-run endpoint and model on the command line**, so a one-off `enrich` can
  use a different model without editing `.env` first.

## Review site

### Edit configuration from the browser

The Markdown criteria files and the `job_sites/` search documents are the
pipeline's whole configuration surface, and both are edited in the review site
now. What is missing is feedback while editing.

- Show what the current word rules would accept or reject, so a change can be
  judged against real rows before it is saved.
- Flag a search whose `params` use a value the `_legend` does not list, so a
  hand-edited file cannot drift away from the dropdowns.

### Trigger pipeline steps from the browser

Buttons to run the backend steps — scrape, filter, enrich — from the review
page, with progress and outcome reported inline instead of in a terminal.

- Run each step independently, matching the CLI's separate commands.
- Stream per-job progress during a scrape and per-job verdicts during
  enrichment.
- Report a failed run with its reason, and allow a run to be stopped.
- One run at a time, since a scrape drives a browser and enrichment loads a
  model into memory.
