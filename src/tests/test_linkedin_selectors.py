"""Field extraction against LinkedIn's markup.

Regression cover for the extractors: Playwright locators are lazy, so the
original try/except around ``page.locator(...)`` could never fire and a
missing element raised out of ``text_content()`` instead of yielding ``None``.
"""

import asyncio

from job_seeker_ai.scrapers import linkedin


class _FakeLocator:
    """A locator that either reports itself attached or times out."""

    def __init__(self, text=None, attached=True, evaluated=None):
        self._text = text
        self._attached = attached
        self._evaluated = evaluated
        self.first = self
        self.waited = []

    async def wait_for(self, state=None, timeout=None):
        self.waited.append((state, timeout))
        if not self._attached:
            raise TimeoutError("selector never became attached")

    async def text_content(self):
        return self._text

    async def evaluate(self, js_code):
        return self._evaluated


class _FakePage:
    def __init__(self, locator):
        self._locator = locator
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        return self._locator


def test_get_text_content_returns_trimmed_text():
    page = _FakePage(_FakeLocator(text="  Senior C# Engineer  "))
    assert asyncio.run(linkedin.get_text_content(page, "h1")) == "Senior C# Engineer"


def test_get_text_content_returns_none_when_the_element_is_missing():
    """The bug: this used to raise instead of returning None."""
    page = _FakePage(_FakeLocator(attached=False))
    assert asyncio.run(linkedin.get_text_content(page, "h1")) is None


def test_get_text_content_normalises_an_empty_element():
    page = _FakePage(_FakeLocator(text=None))
    assert asyncio.run(linkedin.get_text_content(page, "h1")) == ""


def test_get_text_content_awaits_attachment_explicitly():
    locator = _FakeLocator(text="x")
    asyncio.run(linkedin.get_text_content(_FakePage(locator), "h1"))
    assert locator.waited == [("attached", linkedin.SELECTOR_TIMEOUT_MS)]


def test_get_clean_description_returns_none_when_the_element_is_missing():
    page = _FakePage(_FakeLocator(attached=False))
    assert asyncio.run(linkedin.get_clean_description(page, "div")) is None


def test_get_clean_description_returns_the_evaluated_text():
    page = _FakePage(_FakeLocator(evaluated="- bullet\n\nbody"))
    assert asyncio.run(linkedin.get_clean_description(page, "div")) == "- bullet\n\nbody"


def test_wait_for_first_returns_the_locator_when_attached():
    locator = _FakeLocator()
    assert asyncio.run(linkedin._wait_for_first(_FakePage(locator), "x")) is locator


def test_wait_for_first_accepts_a_custom_timeout():
    locator = _FakeLocator()
    asyncio.run(linkedin._wait_for_first(_FakePage(locator), "x", timeout=123))
    assert locator.waited == [("attached", 123)]


def test_job_url_is_stable():
    assert linkedin.job_url("123") == "https://www.linkedin.com/jobs/view/123/"
