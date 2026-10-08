"""Compatibility entry; implementation lives in validation.gitops."""

import sys

import validation.gitops as implementation

sys.modules[__name__] = implementation
