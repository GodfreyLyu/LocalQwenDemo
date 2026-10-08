"""Compatibility entry; implementation lives in dev.initialize_users."""

import sys

import dev.initialize_users as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
