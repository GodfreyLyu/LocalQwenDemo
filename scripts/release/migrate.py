"""Compatibility command; implementation lives in release.migration."""

import importlib
import sys

if __package__ in {None, ""}:
    from _entry import prepare

    prepare()

implementation = importlib.import_module("release.migration")
if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
