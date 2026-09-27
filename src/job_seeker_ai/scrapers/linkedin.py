"""Step 1: LinkedIn scraping via Camoufox + sqlite3.

Camoufox is a stealth-patched Firefox; ``os="macos"`` gives every session a
consistent non-Windows fingerprint and ``humanize=True`` adds human-like
mouse/timing jitter. Both are load-bearing for not being served a login wall.

Structure: search-result pages are opened concurrently, but each job detail
page gets its own browser context so one hung page can be abandoned without
killing the run. SQLite writes are serialised through an asyncio lock, which
is why ``connect()`` sets WAL and a busy timeout.

Every job detail fetch is wrapped in a hard timeout - Playwright can
deadlock on a crashed tab, and without the timeout a single bad posting
hangs the whole run.
"""

import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timezone

from camoufox.async_api import AsyncCamoufox

from job_seeker_ai import config, parsers, repositories
from job_seeker_ai.entities import ScrapedJob
from job_seeker_ai.exceptions import ScrapeError
from job_seeker_ai.scrapers.params import tpr_for_days, translate_params
from job_seeker_ai.storages import connect

logging.getLogger("asyncio").setLevel(logging.CRITICAL)

SITE = "linkedin"

# Max concurrent search pages running at once.
CONCURRENT_SEARCH_PAGES = 2

JOB_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}/"


def job_url(job_id) -> str:
    """Canonical detail-page URL for a site-native job id."""
    return JOB_VIEW_URL.format(job_id=job_id)


def log_header(page_name):
    return f"{datetime.now().strftime('%H:%M:%S')} {page_name}"


async def login():
    """One-time interactive sign-in; persists the session to state.json."""
    async with AsyncCamoufox(headless=False, humanize=True, os="macos") as b:
        if config.STATE_PATH.exists():
            context = await b.new_context(storage_state=str(config.STATE_PATH))
        else:
            context = await b.new_context()

        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")
        email, pw = os.getenv("LI_EMAIL"), os.getenv("LI_PASSWORD")
        if email and pw:
            await page.fill("#username", email)
            await page.fill("#password", pw)
            await page.click("button[type=submit]")

        # Run blocking input in a thread so it doesn't freeze the event loop.
        await asyncio.to_thread(input, "Sign in (solve any checkpoint/2FA), then press Enter here...")

        await page.context.storage_state(path=str(config.STATE_PATH))
        print(f"session saved -> {config.STATE_PATH}")


def effective_tpr(con, configured_tpr):
    """Widen the configured "posted within" window to cover the gap since the
    previous run, so a long pause can't silently skip postings."""
    last_run = repositories.get_meta(con, config.LAST_RUN_META_KEY)
    if not last_run:
        return configured_tpr
    days = max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(last_run)).days)
    return tpr_for_days(days)


def search_url(params, start=0):
    q = {k: v for k, v in params.items() if v not in (None, "")}
    q["start"] = start
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(q)


async def scrape_job(page, job_id, name) -> ScrapedJob:
    attempt = 0

    while True:
        try:
            attempt += 1
            await page.goto(job_url(job_id), wait_until="domcontentloaded", timeout=30000)
        except Exception:
            if attempt < 3:
                print(f"{name}: !! {job_id} load failed, retrying in {5 * (attempt + 1)} seconds...")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            raise
        break

    await page.mouse.wheel(0, 2000)
    await page.wait_for_timeout(250)
    await page.mouse.wheel(2000, 0)
    await page.wait_for_timeout(250)

    await click_on_btn(page, '[aria-label="Dismiss"]')
    await click_on_btn(page, '[aria-label*="more"], [aria-label*="altro"], [aria-label*="mostra"]')

    try:
        await page.wait_for_selector('div[class^="decorated-job-posting"]', timeout=10000)
        await page.wait_for_timeout(500)
    except Exception:
        # Best effort: description extraction reports its own absence. A short
        # page without the expected container is not fatal.
        pass

    title = await get_text_content(page, 'h1[class*="top-card-layout__title"]')
    company = await get_text_content(page, 'a[href*="company"]')
    location = await get_text_content(page, 'span[class*="topcard__flavor--bullet"]')
    posted = await get_text_content(page, 'span[class*="posted-time-ago__text"]')
    desc = await get_clean_description(page, 'div[class^="decorated-job-posting"]')

    return ScrapedJob(
        source_id=job_id,
        url=job_url(job_id),
        title=title,
        company=company,
        location=location,
        posted=posted,
        description=desc,
        salary=parsers.find_salary(desc),
    )


SELECTOR_TIMEOUT_MS = 3000


async def _wait_for_first(page, locator_text, timeout=SELECTOR_TIMEOUT_MS):
    """First matching locator once it is attached, else ``None``.

    Playwright locators are lazy: ``page.locator(...)`` never raises and never
    touches the page, so presence has to be awaited explicitly. Wrapping it
    here is what lets the extractors below return ``None`` on markup drift
    instead of raising.
    """
    locator = page.locator(locator_text).first
    try:
        await locator.wait_for(state="attached", timeout=timeout)
    except Exception:
        return None
    return locator


async def get_text_content(page, locator_text):
    """Text of the first match, or ``None`` if the element never appeared."""
    locator = await _wait_for_first(page, locator_text)
    if locator is None:
        return None
    # text_content() returns None for an empty element; the original code
    # crashed on that, so normalise to "".
    return ((await locator.text_content()) or "").strip()


async def get_clean_description(page, locator_text):
    element = await _wait_for_first(page, locator_text)
    if element is None:
        return None

    # Inject JS to format the DOM into clean markdown-like text.
    js_code = """
    (el) => {
        let clone = el.cloneNode(true);

        // 1. Remove UI garbage (buttons like "Show more", screen reader text, scripts)
        clone.querySelectorAll('button, .visually-hidden, script, style').forEach(n => n.remove());

        // 2. Format lists with markdown bullets
        clone.querySelectorAll('li').forEach(li => {
            li.prepend('\\n- ');
            li.append('\\n');
        });

        // 3. Add spacing to block elements to prevent mashed words
        clone.querySelectorAll('p, div, h1, h2, h3, h4').forEach(block => {
            block.prepend('\\n');
            block.append('\\n');
        });
        clone.querySelectorAll('br').forEach(br => br.replaceWith('\\n'));

        // 4. Extract text and compress excessive blank lines
        let text = clone.textContent || '';
        return text.replace(/\\n\\s*\\n/g, '\\n\\n').trim();
    }
    """
    return await element.evaluate(js_code)


async def click_on_btn(page, locator_text):
    """Best-effort click on optional UI (cookie banners, "show more").

    Failure is expected and ignored: these controls are not always present,
    and not clicking one must not abort the job.
    """
    try:
        btn = page.locator(locator_text).first
        if await btn.is_visible():
            # Timeout so a click can't hang the run indefinitely.
            await btn.click(force=True, timeout=4000)
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def job_ids(page, url, name):
    """Site-native ids found on one search-results page, after scrolling."""
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            break
        except Exception as exc:
            if attempt == 2:
                # Only caller catches broadly, so the concrete type is not
                # observable here; a named error makes the CLI message useful.
                raise ScrapeError(f"{name}: search page load failed: {url}") from exc
            print(f"{name}: !! search page load failed, retrying in {3 * (attempt + 1)} seconds...")
            await asyncio.sleep(3 * (attempt + 1))
    await page.wait_for_timeout(3000)

    ids = set()
    for _ in range(6):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(800)
        els = await page.query_selector_all("[data-occludable-job-id], [data-job-id]")
        for el in els:
            jid = (await el.get_attribute("data-occludable-job-id")) or (await el.get_attribute("data-job-id"))
            if jid and jid.isdigit():
                ids.add(jid)
    return sorted(ids)


async def load_new_search_page(page_name, params, p, search_page, job_browser):
    print(f"{log_header(page_name)} loading ")
    uri = search_url(params, start=p * 25)

    try:
        ids = await job_ids(search_page, uri, page_name)
    except Exception as e:
        # Fall through to the retry below with an explicit empty list. This
        # used to assign 0, which crashed on the len() two lines down and made
        # the retry unreachable.
        print(f"{log_header(page_name)} search page failed ({e}), retrying once")
        ids = []

    if len(ids) == 0:
        new_context = new_page = None
        try:
            print(f"{log_header(page_name)} NO jobs found, retrying 1 time in 3 seconds...")
            await asyncio.sleep(3)
            new_context = await job_browser.new_context(storage_state=str(config.STATE_PATH))
            new_page = await new_context.new_page()
            print(f"{log_header(page_name)} loading ")
            ids = await job_ids(new_page, uri, page_name)
        finally:
            if new_page:
                await new_page.close()
            if new_context:
                await new_context.close()

    return ids


async def process_search_page(search_page, job_browser, name, params, p, search_sem, page_sem, db_lock, seen_ids):
    async with search_sem:
        page_name = f"[{name}] page {p + 1}:"
        async with page_sem:
            ids = await load_new_search_page(page_name, params, p, search_page, job_browser)
            print(f"{log_header(page_name)} {len(ids)} jobs found")

        idx = 0
        for jid in ids:
            idx += 1

            # Pre-scrape filter: an id already recorded never reaches the
            # browser, because fetching and parsing the page is the expensive
            # part. `seen_ids` is seeded from the database at the start of the
            # run and extended as this run inserts more. It is a filter rather
            # than a lock - the authoritative check is still the one inside
            # upsert_job, which is what protects against a second process.
            if jid in seen_ids:
                print(f"{log_header(page_name)} {idx}/{len(ids)} - already recorded, skipping")
                continue

            job_context = None
            try:
                job_context = await job_browser.new_context()
                job_page = await job_context.new_page()

                # FORCE a non-negotiable 60-second limit on the entire job.
                scraped = await asyncio.wait_for(
                    scrape_job(job_page, jid, page_name),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                print(f"{log_header(page_name)}: !! {jid}: Hard timeout exceeded (Playwright deadlock), abandoning job")
                continue
            except Exception as e:
                print(f"{log_header(page_name)}: !! {jid}: {e}")
                continue
            finally:
                if job_context:
                    try:
                        # Prevent context.close() from hanging forever if the tab crashed.
                        await asyncio.wait_for(job_context.close(), timeout=5.0)
                    except Exception:
                        pass

            async with db_lock:
                con = connect()
                job_id = repositories.upsert_job(con, scraped, name, SITE)
                con.close()

            # Either way this id is now recorded, so nothing else in this run
            # needs to look at it again.
            seen_ids.add(jid)

            if job_id is not None:
                print(f"{log_header(page_name)} {idx}/{len(ids)} - {scraped.title} @ {scraped.company}")
            else:
                print(f"{log_header(page_name)} {idx}/{len(ids)} - already recorded, skipping")
            await asyncio.sleep(1)


async def run(specs, headless=True):
    """Scrape every ``SearchSpec`` into the database.

    This calls ``login()`` first, so every run opens a headful browser and waits
    for the operator to finish signing in; invoking ``run`` on its own is not
    currently possible without that prompt.
    """
    con = connect()

    search_sem = asyncio.Semaphore(CONCURRENT_SEARCH_PAGES)
    page_sem = asyncio.Semaphore(1)  # Wait for loading complete before opening new search page
    db_lock = asyncio.Lock()  # Ensures SQLite handles concurrent writes properly

    # Every id already on record, loaded once. This is the pre-scrape filter:
    # it saves the expensive page fetch for postings we already hold, without a
    # database round trip per candidate. It is not a lock - upsert_job still
    # owns the authoritative check.
    seen_ids = repositories.known_source_ids(con, SITE)

    await login()

    print("waiting 3 seconds...")
    await asyncio.sleep(3)

    async with AsyncCamoufox(headless=headless, humanize=True, os="macos") as b:
        search_context = await b.new_context(storage_state=str(config.STATE_PATH))
        search_page = await search_context.new_page()

        tasks = []
        for spec in specs:
            name, params, pages = spec.name, translate_params(spec.params), spec.pages
            if "f_TPR" in params:
                params["f_TPR"] = effective_tpr(con, params["f_TPR"])

            # Queue up all the pages for parallel execution.
            for p in range(pages):
                tasks.append(
                    process_search_page(search_page, b, name, params, p, search_sem, page_sem, db_lock, seen_ids)
                )

        await asyncio.gather(*tasks)

    repositories.set_meta(con, config.LAST_RUN_META_KEY, datetime.now(timezone.utc).isoformat())
    con.close()
