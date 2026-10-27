"""Alias of tools/validate_cases.py, kept because TAREAS.md and the eval-nuevo skill cite it."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parents[1] / "tools" / "validate_cases.py"), run_name="__main__")
