"""Enables ``python -m job_seeker_ai``.

The ``__name__`` guard keeps a plain ``import job_seeker_ai.__main__`` from
running the CLI as a side effect.
"""

import sys

from job_seeker_ai.cli import main

if __name__ == "__main__":
    sys.exit(main())
