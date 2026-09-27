"""Error vocabulary for the pipeline.

Kept intentionally small. A single base class lets callers that only care
about "something in this package failed" use one ``except`` clause, while
specific subclasses let the CLI report what actually went wrong.

Each subclass marks a different way the pipeline can fail: a scrape that gave
up, a browser that is not there, and an evaluation that produced no verdict.
None of them may be mistaken for success.
"""


class JobSeekerError(Exception):
    """Base class for every error this package raises."""


class ScrapeError(JobSeekerError):
    """A site page could not be fetched after all retries."""


class BrowserNotInstalledError(JobSeekerError):
    """The Camoufox browser build is missing and installing it did not work.

    ``pip install camoufox`` installs the Python wrapper only; the browser is a
    separate download. This is an environment problem, not a scrape failure, so
    it gets its own type - and a message telling the user what to run.
    """


class EnrichmentError(JobSeekerError):
    """An evaluation produced no usable verdict.

    Raised instead of returning a sentinel so the caller cannot accidentally
    store "no verdict" as if it were a result. A job that raises this stays
    pending (``enriched_at IS NULL``) and is retried on the next pass.
    """
