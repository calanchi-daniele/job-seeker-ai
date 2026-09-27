"""Step 1 orchestration: searches.json loading and search-page recovery."""

import asyncio
import json

import pytest

from job_seeker_ai.exceptions import BrowserNotInstalledError, ScrapeError
from job_seeker_ai.scrapers import linkedin
from job_seeker_ai.services import scraping


class _FakePage:
    async def close(self):
        return None


class _FakeContext:
    async def new_page(self):
        return _FakePage()

    async def close(self):
        return None


class _FakeBrowser:
    """Just enough of a Camoufox browser for the retry path."""

    def __init__(self):
        self.contexts = 0

    async def new_context(self, **kwargs):
        self.contexts += 1
        return _FakeContext()


def _no_sleep(monkeypatch):
    """The retry path sleeps 3s; drop it so the suite stays fast."""

    async def _sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)


# --- searches.json -------------------------------------------------------


def test_load_searches_reads_every_field(tmp_path):
    path = tmp_path / "searches.json"
    path.write_text(
        json.dumps({"searches": [{"name": "a", "pages": 3, "params": {"keywords": "py"}}]}),
        encoding="utf-8",
    )

    specs = scraping.load_searches(path)

    assert len(specs) == 1
    assert specs[0].name == "a"
    assert specs[0].pages == 3
    assert specs[0].params == {"keywords": "py"}


def test_load_searches_defaults_pages_to_one(tmp_path):
    path = tmp_path / "searches.json"
    path.write_text(json.dumps({"searches": [{"name": "a", "params": {}}]}), encoding="utf-8")

    assert scraping.load_searches(path)[0].pages == 1


def test_load_searches_decodes_utf8(tmp_path):
    path = tmp_path / "searches.json"
    path.write_text(
        json.dumps({"searches": [{"name": "eu", "params": {"location": "Città"}}]}),
        encoding="utf-8",
    )

    assert scraping.load_searches(path)[0].params["location"] == "Città"


def test_load_searches_fails_loudly_on_a_missing_key(tmp_path):
    """A malformed file must not look like "no new jobs"."""
    path = tmp_path / "searches.json"
    path.write_text(json.dumps({"searches": [{"params": {}}]}), encoding="utf-8")

    with pytest.raises(KeyError):
        scraping.load_searches(path)


def test_the_shipped_searches_file_parses():
    specs = scraping.load_searches()
    assert specs
    assert all(spec.name for spec in specs)


# --- search page recovery ------------------------------------------------


def test_search_page_failure_retries_with_a_fresh_context(monkeypatch):
    """The failure branch used to assign 0, which crashed on ``len(ids)``
    before the retry could run. A failed page must now be retried once.
    """
    calls = {"n": 0}

    async def flaky_job_ids(page, url, name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ScrapeError("search page load failed")
        return ["42"]

    monkeypatch.setattr(linkedin, "job_ids", flaky_job_ids)
    _no_sleep(monkeypatch)
    browser = _FakeBrowser()

    ids = asyncio.run(linkedin.load_new_search_page("t", {}, 0, object(), browser))

    assert ids == ["42"]
    assert calls["n"] == 2
    assert browser.contexts == 1


def test_empty_search_page_also_retries(monkeypatch):
    """A page that loads but yields nothing is worth one retry too."""
    calls = {"n": 0}

    async def empty_then_hit(page, url, name):
        calls["n"] += 1
        return [] if calls["n"] == 1 else ["7"]

    monkeypatch.setattr(linkedin, "job_ids", empty_then_hit)
    _no_sleep(monkeypatch)

    ids = asyncio.run(linkedin.load_new_search_page("t", {}, 0, object(), _FakeBrowser()))

    assert ids == ["7"]


def test_search_page_failing_twice_is_not_swallowed(monkeypatch):
    """The retry is not a blanket catch: a second failure propagates so the
    run reports it instead of quietly scraping nothing."""

    async def always_fails(page, url, name):
        raise ScrapeError("still failing")

    monkeypatch.setattr(linkedin, "job_ids", always_fails)
    _no_sleep(monkeypatch)

    with pytest.raises(ScrapeError):
        asyncio.run(linkedin.load_new_search_page("t", {}, 0, object(), _FakeBrowser()))


def test_retry_closes_the_temporary_context(monkeypatch):
    """The fresh context must be closed even when the retry itself fails."""

    async def always_fails(page, url, name):
        raise ScrapeError("boom")

    monkeypatch.setattr(linkedin, "job_ids", always_fails)
    _no_sleep(monkeypatch)

    closed = []
    original = _FakeContext.close

    async def tracking_close(self):
        closed.append(True)
        return await original(self)

    monkeypatch.setattr(_FakeContext, "close", tracking_close)

    with pytest.raises(ScrapeError):
        asyncio.run(linkedin.load_new_search_page("t", {}, 0, object(), _FakeBrowser()))

    assert closed == [True]


# --- the phase preflights the environment once ---------------------------


class _RecordingAdapter:
    """Records what the phase called, and in which order."""

    def __init__(self, events):
        self._events = events

    async def run(self, specs, headless=True):
        self._events.append("adapter.run")

    async def login(self):
        self._events.append("adapter.login")


def _patch_phase(monkeypatch, events, adapter):
    monkeypatch.setattr(scraping.browsers, "ensure_camoufox_installed", lambda: events.append("preflight"))
    monkeypatch.setattr(scraping, "get_adapter", lambda site: adapter)
    monkeypatch.setattr(scraping, "load_searches", lambda *args, **kwargs: [])


def test_run_preflights_once_before_reaching_the_adapter(monkeypatch):
    events = []
    _patch_phase(monkeypatch, events, _RecordingAdapter(events))

    asyncio.run(scraping.run())

    assert events == ["preflight", "adapter.run"]


def test_login_preflights_once_before_reaching_the_adapter(monkeypatch):
    """`login` is a scraping-phase command too, so it gets the same guarantee."""
    events = []
    _patch_phase(monkeypatch, events, _RecordingAdapter(events))

    asyncio.run(scraping.login())

    assert events == ["preflight", "adapter.login"]


def test_the_adapter_is_not_reached_when_the_preflight_fails(monkeypatch):
    """A missing browser must stop the phase before any site is opened."""
    events = []

    def explode():
        events.append("preflight")
        raise BrowserNotInstalledError("no browser")

    monkeypatch.setattr(scraping.browsers, "ensure_camoufox_installed", explode)
    monkeypatch.setattr(scraping, "get_adapter", lambda site: _RecordingAdapter(events))
    monkeypatch.setattr(scraping, "load_searches", lambda *args, **kwargs: [])

    with pytest.raises(BrowserNotInstalledError):
        asyncio.run(scraping.run())

    assert events == ["preflight"]
