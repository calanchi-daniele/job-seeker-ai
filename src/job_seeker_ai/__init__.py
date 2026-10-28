"""job_seeker_ai - a local-first LinkedIn job pipeline.

Four independent steps, each usable on its own:

    1. scrape   job_sites/*.json     -> jobs.db   (scrapers/)
    2. filter   hard word rules      -> jobs.db   (filters/)
    3. enrich   API evaluation        -> jobs.db   (evaluators/)
    4. review   read/triage jobs.db  -> browser   (web/)

Steps 1, 2 and 4 run entirely on this machine. Step 3 is an OpenAI-compatible API
client: it needs an endpoint serving the model, and that endpoint is yours to run
and is never started, watched or stopped from here.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
