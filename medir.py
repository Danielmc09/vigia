"""Entry point of specs/06 (``python medir.py ...``); the code lives in tools/measure.py."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "tools" / "measure.py"), run_name="__main__")
