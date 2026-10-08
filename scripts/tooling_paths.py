"""Stable checkout paths shared by command wrappers and internal tooling."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_backend():
    """Allow standalone tooling to import the checkout's backend package."""
    path = str(ROOT / "backend")
    if path not in sys.path:
        sys.path.insert(0, path)
