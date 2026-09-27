"""job_seeker_ai - a local-first LinkedIn job pipeline.

Four independent steps, each usable on its own:

    1. scrape   searches.json        -> jobs.db   (scrapers/)
    2. filter   hard word rules      -> jobs.db   (filters/)
    3. enrich   local LLM evaluation -> jobs.db   (evaluators/)
    4. review   read/triage jobs.db  -> browser   (web/)

Nothing here talks to a cloud API: the only LLM is a local LM Studio
server started and stopped on demand by the enrichment step.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
