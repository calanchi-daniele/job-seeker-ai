"""Single source of truth for paths, environment lookups and run-wide values.

Every path is derived from ``PROJECT_ROOT``, which is computed from this
file's own location. That matters: the previous layout derived the criteria
directory from the *scrapers* directory, which never contained it, so the
enrichment step could not find its rules at all. Resolving every path from this
one module is what fixed that.

Paths and overrides are resolved once, at import time, after ``.env`` has been
loaded from the project root - see ``.env.example`` for the full list. Tests must
therefore set the environment overrides *before* importing anything from this
package; see ``src/tests/conftest.py``.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# src/job_seeker_ai/config.py -> src/job_seeker_ai -> src -> <project root>
MODULE_DIR = Path(__file__).resolve().parent
SRC_DIR = MODULE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

# .env at the project root, loaded before anything below reads the environment,
# so a value in the file and an exported variable are interchangeable. A real
# environment variable wins over the file - an explicit export has to beat a
# stale checkout of .env - and a missing file is not an error, it is the default
# setup. DOTENV_PATH is only a test hook; the variables in .env.example have no
# equivalent, because a hook for each of them would be a second config surface to
# document and keep in step.
ENV_PATH = Path(os.environ["DOTENV_PATH"]) if os.environ.get("DOTENV_PATH") else PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


def _env_path(name: str, default: Path) -> Path:
    """Return ``$name`` as a Path when set and non-empty, else ``default``."""
    value = os.environ.get(name)
    return Path(value) if value else default


# --- runtime state -------------------------------------------------------
# state.json holds live LinkedIn session cookies. It is listed in
# .gitignore and must never be committed or pasted into an issue.
STATE_PATH = _env_path("STATE_PATH", PROJECT_ROOT / "state.json")

# --- user configuration (hand-edited, versioned with the project) --------
SEARCHES_PATH = _env_path("SEARCHES_PATH", PROJECT_ROOT / "searches.json")
CRITERIA_DIR = _env_path("CRITERIA_DIR", PROJECT_ROOT / "criteria")

MUST_WORDS_PATH = CRITERIA_DIR / "must_words.md"
FORBIDDEN_WORDS_PATH = CRITERIA_DIR / "forbidden_words.md"
DEAL_BREAKERS_PATH = CRITERIA_DIR / "deal_breakers.md"

# --- scraped data -------------------------------------------------------
# JOBS_DB_PATH exists so tests can point at a throwaway database instead of
# the real jobs.db. It is not a multi-database runtime feature.
DB_PATH = _env_path("JOBS_DB_PATH", PROJECT_ROOT / "jobs.db")

# --- presentation assets -------------------------------------------------
TEMPLATES_DIR = MODULE_DIR / "web" / "templates"
STATIC_DIR = MODULE_DIR / "web" / "static"

# --- the model API (OpenAI-compatible) -----------------------------------
# Step 3 is a plain API client: it sends a posting to an OpenAI-compatible
# endpoint and stores the typed verdict that comes back. Nothing in this project
# loads a model, holds weights or starts a server - whatever serves the endpoint
# is yours to run, and all this needs is to reach it.
#
# All three are required, and there is deliberately no default endpoint: aiming
# this pipeline at a provider is something you ask for by name in .env, so a
# missing value is reported before the first request instead of quietly choosing
# a host for you. ``llm_clients.check_config`` names whichever is unset.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# How many deal-breaker evaluations may be in flight at once. 1 keeps them
# strictly sequential, which is the polite setting for a rate-limited key; raise
# it when the endpoint tolerates parallel requests.
LLM_MAX_CONCURRENCY = max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "1")))

# --- run-wide values -----------------------------------------------------
LAST_RUN_META_KEY = "last_run_at"
