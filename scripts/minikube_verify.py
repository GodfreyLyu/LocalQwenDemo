"""Compatibility entry; implementation lives in deployment.legacy.verify."""

import sys

import deployment.legacy.verify as implementation

sys.modules[__name__] = implementation
