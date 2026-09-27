"""Browser-engine installation plumbing.

The scraping phase calls these once, before any adapter opens a browser, so that
individual site adapters carry no install logic of their own: a new adapter
inherits the guarantee for free, and the check is not repeated per site or per
launch.

``pip install camoufox`` ships the Python wrapper only - the browser is a
separate, large download. Launching without it fails deep inside the library
with a message that does not say what to do, which is the whole reason this
preflight exists.
"""

import subprocess
import sys

from camoufox import pkgman
from camoufox.pkgman import CamoufoxNotInstalled

from job_seeker_ai.exceptions import BrowserNotInstalledError


def _installed_camoufox_version() -> str | None:
    """The installed Camoufox build, or ``None`` when it is missing.

    Split out from the installer so both branches can be tested without
    touching the filesystem or the network.
    """
    try:
        return pkgman.installed_verstr()
    except CamoufoxNotInstalled:
        return None


def ensure_camoufox_installed() -> str:
    """Return the installed Camoufox version, installing the browser if needed.

    Cheap and idempotent when the browser is already there - a filesystem probe,
    no network - so the scraping phase can call it unconditionally at startup.
    """
    version = _installed_camoufox_version()
    if version:
        return version

    print("Camoufox browser not found - installing it with `camoufox fetch` ...")
    # sys.executable rather than a bare `camoufox`: the console script is not
    # guaranteed to be on PATH, but the venv's interpreter always is.
    subprocess.run([sys.executable, "-m", "camoufox", "fetch"], check=True)

    version = _installed_camoufox_version()
    if not version:
        raise BrowserNotInstalledError(
            "`camoufox fetch` did not leave a usable browser build. Run it by hand to see why: python -m camoufox fetch"
        )
    print(f"Camoufox browser {version} ready")
    return version
