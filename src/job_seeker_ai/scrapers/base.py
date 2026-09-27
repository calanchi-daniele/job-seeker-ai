"""The contract every site adapter fulfils, plus the registry that finds one.

An adapter is a *module*, not a class. Each one exposes three names:

``SITE``
    The short site key stored in ``job_sources.site`` (e.g. ``"linkedin"``).
``async login()``
    Interactive, one-time: open a browser, let the human sign in, persist the
    session so later runs are unattended.
``async run(specs, headless=True)``
    Scrape every ``SearchSpec`` and persist each posting. Returns when done.

Modules rather than classes on purpose: adapters are stateless collections of
functions, so a class would add ceremony without adding behaviour. The
registry keeps the import lazy, which is what stops ``--help`` and the
word-filter step from needing a browser stack installed.

**Adapters do no environment setup.** The scraping phase runs its preflight
(``services.scraping.prepare``) once before calling into an adapter, so anything
an adapter needs installed - a browser build, a driver - is already there by the
time it launches. Do not add an install check to an adapter: add it to
``prepare`` instead, and every site gets it once rather than per launch.
"""

import importlib

from job_seeker_ai.exceptions import JobSeekerError

REGISTRY = {
    "linkedin": "job_seeker_ai.scrapers.linkedin",
}


def get_adapter(site: str):
    """Import and return the adapter module registered under ``site``."""
    module_path = REGISTRY.get(site)
    if module_path is None:
        known = ", ".join(sorted(REGISTRY))
        raise JobSeekerError(f"unknown site {site!r} (known sites: {known})")
    return importlib.import_module(module_path)
