#!/usr/bin/env python3
"""Thin script so you can symlink it to /usr/local/bin/bp1pro."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bp1pro.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
