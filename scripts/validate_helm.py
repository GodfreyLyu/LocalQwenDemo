"""Compatibility entry; implementation lives in validation.helm."""

import sys

import validation.helm as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
