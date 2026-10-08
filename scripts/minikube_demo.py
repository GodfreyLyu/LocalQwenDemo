"""Compatibility entry; implementation lives in deployment.legacy.runtime."""

import sys

import deployment.legacy.runtime as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
