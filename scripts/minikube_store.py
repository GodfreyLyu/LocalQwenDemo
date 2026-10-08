"""Compatibility entry; implementation lives in deployment.legacy.store."""

import sys

import deployment.legacy.store as implementation

sys.modules[__name__] = implementation
