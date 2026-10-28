"""Alias of tools/sentinel.py, kept because TAREAS.md and specs/11 cite it."""

import runpy
import sys
from pathlib import Path

TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))  # sentinel imports measure, a sibling in tools/
runpy.run_path(str(TOOLS / "sentinel.py"), run_name="__main__")
