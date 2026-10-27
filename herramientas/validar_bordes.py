"""Alias of tools/validate_edges.py, kept because TAREAS.md (T-05b) cites it."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parents[1] / "tools" / "validate_edges.py"), run_name="__main__")
