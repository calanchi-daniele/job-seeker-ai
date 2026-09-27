#!/usr/bin/env bash
# Convenience wrapper: run the whole pipeline in order.
#
# This is a thin shim over the Python CLI - it exists so a cron job, a git
# hook or a human has one command. The real entry point is `job_seeker_ai`;
# see `python -m job_seeker_ai --help` for the individual steps.
#
# Requires bash (Git Bash/WSL on Windows). For PowerShell, run the three
# commands from the README directly.
#
# Usage:
#   ./run_all.sh             # scrape (headless) + filter + enrich
#   ./run_all.sh --headful   # same, but watch the browser while scraping
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-.venv/bin/python}"

# Allows ./run_all.sh to work without an editable install. Installing with
# `pip install -e .` makes this unnecessary.
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src"

"$PY" -m job_seeker_ai run "$@"
echo
echo "--- scrape done, running word filter ---"
echo
"$PY" -m job_seeker_ai filter
echo
echo "--- filter done, enriching pending jobs with the local LLM ---"
echo
"$PY" -m job_seeker_ai enrich
