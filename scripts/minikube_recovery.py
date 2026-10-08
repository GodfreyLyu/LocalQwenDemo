"""Compatibility entry; implementation lives in deployment.legacy.recovery."""

import sys

import deployment.legacy.recovery as implementation

sys.modules[__name__] = implementation
