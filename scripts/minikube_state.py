"""Compatibility entry; implementation lives in deployment.legacy.state."""

import sys

import deployment.legacy.state as implementation

sys.modules[__name__] = implementation
