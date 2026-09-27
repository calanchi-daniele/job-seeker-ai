"""Plain data carriers passed between pipeline steps.

Deliberately boring: no behaviour, no ORM, no validation. Each step
receives one of these and returns one of these, so the modules that do the
work can be tested without a browser or a database.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchSpec:
    """One entry of ``searches.json``.

    ``params`` stays a plain dict because it is whatever the search
    definition holds; translating the friendly names into the site's own
    facet codes is a site-adapter concern (see ``scrapers/params.py``).
    """

    name: str
    params: dict
    pages: int = 1


@dataclass(frozen=True)
class ScrapedJob:
    """One posting exactly as it was read off a site page."""

    source_id: str
    url: str
    title: str | None
    company: str | None
    location: str | None
    posted: str | None
    description: str | None
    salary: str | None
