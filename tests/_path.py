"""Shared sys.path setup so tests import the app modules from src/ and scripts/.

Imported for side effects at the top of each test module, so the suite runs under
both `python3 -m unittest discover -s tests` and `pytest`.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "src", _ROOT / "scripts"):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)
