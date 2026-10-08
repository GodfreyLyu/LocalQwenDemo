"""Compatibility entry; implementation lives in dev.harness."""

import sys

import dev.harness as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
