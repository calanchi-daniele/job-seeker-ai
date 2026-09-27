"""Package-wide guards for failure modes that are easy to reintroduce.

These are cheap regression fences for defects that no single unit test can
catch, because they are about how the code is written rather than what it does.
"""

import re
from pathlib import Path

import job_seeker_ai

PACKAGE_ROOT = Path(job_seeker_ai.__file__).parent

# `except:` / `except:  # comment` with nothing else on the line
BARE_EXCEPT = re.compile(r"^\s*except\s*:\s*(#.*)?$")

# read_text()/write_text() with no encoding= argument, which silently uses the
# locale encoding - the cause of the criteria mojibake bug
TEXT_IO_WITHOUT_ENCODING = re.compile(r"\.(read_text|write_text)\(\s*\)")


def _source_files():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_no_bare_except_clauses():
    """A bare ``except:`` also swallows KeyboardInterrupt and SystemExit, so
    Ctrl-C cannot stop a long scrape."""
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in _source_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE_EXCEPT.match(line)
    ]
    assert not offenders, f"bare except: {offenders}"


def test_no_text_io_without_an_explicit_encoding():
    """The criteria files contain ``€``; the locale encoding on Windows is
    cp1252, which mangles it before it reaches the LLM prompt."""
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in _source_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if TEXT_IO_WITHOUT_ENCODING.search(line)
    ]
    assert not offenders, f"text I/O with no encoding=: {offenders}"


def test_package_does_not_manipulate_sys_path():
    """Imports must work through the installed package, not a sys.path hack."""
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in _source_files()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "sys.path" in line
    ]
    assert not offenders, f"sys.path manipulation: {offenders}"


def test_services_do_not_import_web_frameworks():
    """The pipeline must be usable with no web extra installed."""
    forbidden = ("fastapi", "uvicorn", "starlette", "jinja2")
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in (PACKAGE_ROOT / "services").rglob("*.py")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(f"import {name}" in line or f"from {name}" in line for name in forbidden)
    ]
    assert not offenders, f"services importing a web framework: {offenders}"


def test_only_repositories_writes_data_sql():
    """Keeps the query surface in one reviewable place.

    Connection pragmas (``storages.py``) and the DDL script are storage-layer
    concerns and are allowed; SELECT/INSERT/UPDATE/DELETE are not.
    """
    assert not _data_sql_offenders(), f"data SQL outside repositories.py: {_data_sql_offenders()}"


SQL_CALL = re.compile(r"\.exe(cute|cutemany|utescript)\(")
SQL_KEYWORD = re.compile(r"^\s*f?[\"']\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)
SQL_ALLOWED_MODULES = {"repositories.py"}


def _data_sql_offenders():
    """Find SQL calls whose statement starts on this or the next line."""
    offenders = []
    for path in _source_files():
        if path.name in SQL_ALLOWED_MODULES:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not SQL_CALL.search(line):
                continue
            tail = line[line.index("(") + 1 :]
            fragment = tail if tail.strip() else (lines[index + 1] if index + 1 < len(lines) else "")
            if SQL_KEYWORD.match(fragment):
                offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{index + 1}")
    return offenders
