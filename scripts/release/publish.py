"""Compatibility command; implementation lives in release.publisher."""

import importlib
import sys

if __package__ in {None, ""}:
    from _entry import prepare

    prepare()

implementation = importlib.import_module("release.publisher")
if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
