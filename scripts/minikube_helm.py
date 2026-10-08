"""Compatibility entry; implementation lives in deployment.helm.cli."""

import sys

import deployment.helm.cli as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
