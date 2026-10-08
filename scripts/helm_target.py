"""Compatibility entry; implementation lives in deployment.common.target."""

import sys

import deployment.common.target as implementation

sys.modules[__name__] = implementation
