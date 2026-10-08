"""Compatibility entry; implementation lives in deployment.helm.verify."""

import sys

import deployment.helm.verify as implementation

sys.modules[__name__] = implementation
