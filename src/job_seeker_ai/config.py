"""Single source of truth for paths, environment lookups and run-wide values.

Every path is derived from ``PROJECT_ROOT``, which is computed from this
file's own location. That matters: the previous layout derived the criteria
directory from the *scrapers* directory, which never contained it, so the
enrichment step could not find its rules at all. Resolving every path from this
one module is what fixed that.

Paths are resolved once, at import time. Tests must therefore set the
environment overrides *before* importing anything from this package; see
``src/tests/conftest.py``.
"""

import os
from pathlib import Path

# src/job_seeker_ai/config.py -> src/job_seeker_ai -> src -> <project root>
MODULE_DIR = Path(__file__).resolve().parent
SRC_DIR = MODULE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent


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

# --- local LLM (LM Studio, OpenAI-compatible) ----------------------------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "lm-studio")
# Confirmed against the local LM Studio install; override per machine.
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.8-27b")

# --- run-wide values -----------------------------------------------------
LAST_RUN_META_KEY = "last_run_at"
