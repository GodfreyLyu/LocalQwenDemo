"""Compatibility entry; implementation lives in deployment.helm.lifecycle."""

import sys

import deployment.helm.lifecycle as implementation

sys.modules[__name__] = implementation
