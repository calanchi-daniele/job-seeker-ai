"""The scraping phase's browser preflight.

``pip install camoufox`` ships the Python wrapper only; the browser is a separate
large download that the adapter needs at launch. These cover the check itself,
and the invariant that it happens *once*, at the phase boundary, rather than
inside every adapter or at every launch.

Nothing here downloads anything: the version probe and the installer call are
both stubbed.
"""

import ast
import subprocess
from pathlib import Path

import pytest

from job_seeker_ai import browsers, exceptions

INSTALLED = "152.0.4-beta.31"
PACKAGE_ROOT = Path(browsers.__file__).parent


class _VersionProbe:
    """Stands in for the installed-version check.

    Consumes the queued values, then holds the last one - so ``(None, "1.0")``
    means "missing first, then installed for good", and a single value means
    "always that", which is what a real filesystem probe does.
    """

    def __init__(self, *versions):
        self._queued = list(versions)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self._queued:
            return None
        if len(self._queued) > 1:
            return self._queued.pop(0)
        return self._queued[0]


class _Recorder:
    """Records installer invocations instead of running them."""

    def __init__(self):
        self.commands = []

    def __call__(self, command, check=False):
        self.commands.append((list(command), check))


def _patch_probe(monkeypatch, *versions):
    probe = _VersionProbe(*versions)
    monkeypatch.setattr(browsers, "_installed_camoufox_version", probe)
    return probe


def _patch_installer(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


# --- behaviour -----------------------------------------------------------


def test_returns_the_version_when_already_installed(monkeypatch):
    _patch_probe(monkeypatch, INSTALLED)
    installer = _patch_installer(monkeypatch)

    assert browsers.ensure_camoufox_installed() == INSTALLED
    # already present: no download, and no network round trip
    assert installer.commands == []


def test_installs_when_missing(monkeypatch):
    _patch_probe(monkeypatch, None, INSTALLED)
    installer = _patch_installer(monkeypatch)

    assert browsers.ensure_camoufox_installed() == INSTALLED
    assert len(installer.commands) == 1
    assert installer.commands[0][1] is True  # check=True, so a failure surfaces


def test_installer_runs_the_documented_fetch_command(monkeypatch):
    _patch_probe(monkeypatch, None, INSTALLED)
    installer = _patch_installer(monkeypatch)

    browsers.ensure_camoufox_installed()

    command, _ = installer.commands[0]
    # the venv interpreter, not a bare `camoufox` that may not be on PATH
    assert command[0] == browsers.sys.executable
    assert command[1:] == ["-m", "camoufox", "fetch"]


def test_raises_a_clear_error_when_the_install_does_not_help(monkeypatch):
    _patch_probe(monkeypatch, None, None)
    _patch_installer(monkeypatch)

    with pytest.raises(exceptions.BrowserNotInstalledError) as excinfo:
        browsers.ensure_camoufox_installed()

    # the message has to say what to do next
    assert "camoufox fetch" in str(excinfo.value)


def test_never_installs_more_than_once(monkeypatch):
    _patch_probe(monkeypatch, None, None)
    installer = _patch_installer(monkeypatch)

    with pytest.raises(exceptions.BrowserNotInstalledError):
        browsers.ensure_camoufox_installed()

    assert len(installer.commands) == 1


def test_version_probe_reports_missing_rather_than_raising(monkeypatch):
    """The probe must not leak the library's exception to its callers."""

    def raise_not_installed():
        raise browsers.CamoufoxNotInstalled("not installed")

    monkeypatch.setattr(browsers.pkgman, "installed_verstr", raise_not_installed)

    assert browsers._installed_camoufox_version() is None


def test_version_probe_passes_a_real_version_through(monkeypatch):
    monkeypatch.setattr(browsers.pkgman, "installed_verstr", lambda: INSTALLED)

    assert browsers._installed_camoufox_version() == INSTALLED


# --- the preflight belongs to the phase, not to the adapters -------------


def _sources(folder):
    return sorted((PACKAGE_ROOT / folder).rglob("*.py"))


def _calls_to(path, suffix):
    """Line numbers of calls whose callee name ends with ``suffix``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(suffix)
    ]


def test_the_phase_calls_the_preflight_exactly_once():
    """Once per command, however many sites there are - that is the point."""
    calls = _calls_to(PACKAGE_ROOT / "services" / "scraping.py", "ensure_camoufox_installed")
    assert len(calls) == 1, f"expected exactly one preflight call, found lines {calls}"


def test_no_adapter_calls_the_preflight():
    """Adapters must not re-check the environment at their launch sites."""
    offenders = [
        f"{path.name}:{line}" for path in _sources("scrapers") for line in _calls_to(path, "ensure_camoufox_installed")
    ]
    assert not offenders, f"adapters calling the preflight: {offenders}"


def test_no_adapter_shells_out_to_install_anything():
    """``subprocess`` in an adapter would mean install logic crept back in."""
    offenders = [f"{path.name}:{line}" for path in _sources("scrapers") for line in _calls_to(path, "subprocess.run")]
    assert not offenders, f"adapters spawning installers: {offenders}"


def test_no_adapter_imports_the_preflight_module():
    for path in _sources("scrapers"):
        source = path.read_text(encoding="utf-8")
        assert "job_seeker_ai.browsers" not in source, path.name
