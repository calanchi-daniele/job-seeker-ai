"""Command-line entry point: ``python -m job_seeker_ai <command>``.

One dispatcher for the whole pipeline so a reader (or a CI job) has a single
place to look for "how do I run this". Every heavy import happens inside the
command that needs it: ``--help`` and the word-filter step must not require a
browser stack or FastAPI to be installed.

Run ``python -m job_seeker_ai --help`` for the command list.
"""

import argparse
import asyncio

from job_seeker_ai import __version__

PROGRAM = "job-seeker-ai"


def _cmd_login(args):
    """Interactive, one-time: sign in and persist the session."""
    from job_seeker_ai.services import scraping

    asyncio.run(scraping.login(site=args.site))
    return 0


def _cmd_run(args):
    """Step 1: scrape every configured search into the database."""
    from job_seeker_ai.services import scraping

    asyncio.run(scraping.run(site=args.site, headless=not args.headful))
    return 0


def _cmd_filter(args):
    """Step 2: hard word filter, no LLM."""
    from job_seeker_ai.services import filtering

    filtering.run(dry_run=args.dry_run)
    return 0


def _cmd_enrich(args):
    """Step 3: local-LLM evaluation of the deal breakers."""
    from job_seeker_ai.services import enrichment

    enrichment.start_enrich_process()
    return 0


def _cmd_serve(args):
    """Step 4: the review website."""
    import uvicorn

    uvicorn.run("job_seeker_ai.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Scrape, filter, enrich and review job postings locally.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="sign in once and save the session")
    p_login.add_argument("--site", default="linkedin", help="site adapter to use (default: linkedin)")
    p_login.set_defaults(handler=_cmd_login)

    p_run = sub.add_parser("run", help="scrape searches.json into the database")
    p_run.add_argument("--site", default="linkedin", help="site adapter to use (default: linkedin)")
    p_run.add_argument("--headful", action="store_true", help="show the browser while scraping")
    p_run.set_defaults(handler=_cmd_run)

    p_filter = sub.add_parser("filter", help="apply must/forbidden word rules (no LLM)")
    p_filter.add_argument("--dry-run", action="store_true", help="report rejections without writing")
    p_filter.set_defaults(handler=_cmd_filter)

    p_enrich = sub.add_parser("enrich", help="evaluate pending jobs with the local LLM")
    p_enrich.set_defaults(handler=_cmd_enrich)

    p_serve = sub.add_parser("serve", help="serve the review website")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    p_serve.set_defaults(handler=_cmd_serve)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
