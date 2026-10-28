"""Step 1: turn ``job_sites/`` into scrape work and hand it to an adapter.

This module is the only place that turns a site's search file into ``SearchSpec``
objects. Adapters receive ready-made specs, which is what makes them testable
without a config file and reusable with any other source of specs. The JSON itself
belongs to ``job_sites.py``.

It is also the boundary for the scraping phase's *environment*: ``prepare()``
checks the browser stack once, here, instead of inside every adapter. Adding a
site therefore does not mean adding another install check to a launch path.
"""

from pathlib import Path

from job_seeker_ai import browsers, job_sites
from job_seeker_ai.entities import SearchSpec
from job_seeker_ai.scrapers.base import get_adapter


def load_searches(path: str | Path | None = None) -> list[SearchSpec]:
    """Parse the site's search file. Raises ``KeyError``/``JSONDecodeError`` loudly
    rather than scraping nothing, since a silent empty run looks like
    "no new jobs"."""
    raw = job_sites.load_document(path)
    return [
        SearchSpec(name=s["name"], params=s["params"], pages=s.get("pages", 1))
        for s in raw[job_sites.SEARCHES_KEY]
    ]


def prepare() -> None:
    """Preflight the scraping phase, once per command.

    Both scraping-phase entry points below call this before touching an adapter,
    so the browser is guaranteed present by the time any site opens one. It is a
    filesystem probe when the browser is already installed, so running it on
    every command costs nothing.

    This is the one place to extend when a new engine joins Camoufox - for
    example a Playwright-based adapter needing its own Chromium download.
    """
    browsers.ensure_camoufox_installed()


async def login(site: str = "linkedin"):
    """Interactive sign-in for ``site``, persisted for later unattended runs."""
    prepare()
    adapter = get_adapter(site)
    await adapter.login()


async def run(site: str = "linkedin", headless: bool = True):
    """Scrape every configured search for ``site`` into the database."""
    prepare()
    adapter = get_adapter(site)
    await adapter.run(load_searches(), headless=headless)
