"""Bootstrap old file-based release commands; implementation imports stay qualified."""

import sys
from pathlib import Path


def prepare():
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
