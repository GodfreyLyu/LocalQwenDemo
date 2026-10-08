"""Compatibility entry; implementation lives in validation.manifests."""

import sys

import validation.manifests as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
