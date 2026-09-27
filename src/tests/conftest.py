"""Shared test setup.

Isolation comes from ``JOBS_DB_PATH``: every test run gets a throwaway sqlite
file in the system temp directory, never the real ``jobs.db``.

The environment override MUST be set before anything imports
``job_seeker_ai.config``, because config resolves its paths once at import time.
That is why the assignment sits at module level, above every other import.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="job_seeker_ai_tests_"))
os.environ["JOBS_DB_PATH"] = str(_TMP_DIR / "test_jobs.db")

import pytest  # noqa: E402  (must follow the env override above)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture
def con():
    """A connection to the throwaway database.

    Tests share the one database file, so they use distinct job ids.
    """
    from job_seeker_ai.storages import connect

    connection = connect()
    yield connection
    connection.close()
