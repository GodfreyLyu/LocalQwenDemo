"""Compatibility entry; implementation lives in deployment.legacy.target."""

import sys

import deployment.legacy.target as implementation

sys.modules[__name__] = implementation
