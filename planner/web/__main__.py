"""``python -m planner.web`` entry point.

Routes through :func:`planner.web.scripts_entry.main` so the CLI
flags are identical between the two invocation forms.
"""

from __future__ import annotations

import sys

from .scripts_entry import main


if __name__ == "__main__":
    raise SystemExit(main())