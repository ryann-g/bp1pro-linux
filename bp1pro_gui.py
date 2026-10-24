#!/usr/bin/env python3
"""bp1pro GUI launcher (desktop entry target)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bp1pro.gui import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(mock="--mock" in sys.argv[1:]))
