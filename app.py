from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nanatools import logging_setup
from nanatools.ui.shell import run


def main() -> int:
    log = logging_setup.setup()
    try:
        return run()
    except Exception:
        log.exception("fatal error starting the application")
        raise


if __name__ == "__main__":
    sys.exit(main())
