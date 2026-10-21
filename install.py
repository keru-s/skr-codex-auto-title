#!/usr/bin/env python3
"""Direct hook installer; the skill and terminal use the same implementation."""
from pathlib import Path
import runpy
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
runpy.run_path(str(Path(__file__).resolve().parent / "skills/skr-codex-auto-title/scripts/install.py"), run_name="__main__")
