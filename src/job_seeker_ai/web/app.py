"""Review website: browse/filter/invalidate scraped jobs.

Read-only besides status toggling. This is a separate optional process
(``python -m job_seeker_ai serve``) and is deliberately not wired into the
scrape or enrich runs - it can be served, restarted or dropped at any time
without touching the pipeline.

Kept as a single module on purpose: four endpoints do not need a router
package. All SQL lives in ``repositories``; this module only translates HTTP
into repository calls and hands results to templates.
"""

from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_seeker_ai import config, repositories
from job_seeker_ai.storages import connect

app = FastAPI(title="Job review")
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: str, back: str = "/"):
    con = connect()
    job = repositories.get_job(con, job_id)
    if job is None:
        con.close()
        return RedirectResponse(back, status_code=303)
    sources = repositories.get_job_sources(con, job_id)
    con.close()
    return templates.TemplateResponse(request, "job_detail.html", {"job": job, "sources": sources, "back": back})


@app.get("/")
def job_list(request: Request, status: str | None = None):
    """The list, newest first. ``status`` is the only filter: the score columns
    that used to be filterable and sortable are not in the schema any more."""
    con = connect()
    jobs = repositories.list_jobs(con, status=status)
    sources = repositories.list_sources_grouped(con)
    con.close()

    query = {"status": status} if status is not None else {}
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "sources": sources,
            "filters": {"status": status or ""},
            "querystring": urlencode(query),
        },
    )


def _set_status(job_id: str, status: str, back: str | None):
    con = connect()
    repositories.set_job_status(con, job_id, status)
    con.close()
    return RedirectResponse(back or "/", status_code=303)


@app.post("/jobs/{job_id}/invalidate")
def invalidate(job_id: str, back: str = "/"):
    return _set_status(job_id, "invalidated", back)


@app.post("/jobs/{job_id}/reactivate")
def reactivate(job_id: str, back: str = "/"):
    return _set_status(job_id, "active", back)
