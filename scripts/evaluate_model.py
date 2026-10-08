"""Compatibility entry; implementation lives in evaluation.cli."""

import sys

import evaluation.cli as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
