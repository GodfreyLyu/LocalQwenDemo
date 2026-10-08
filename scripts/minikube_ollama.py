"""Compatibility entry; implementation lives in deployment.legacy.ollama."""

import sys

import deployment.legacy.ollama as implementation

sys.modules[__name__] = implementation
