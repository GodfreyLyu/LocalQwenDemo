"""Compatibility entry; implementation lives in deployment.legacy.undeploy."""

import sys

import deployment.legacy.undeploy as implementation

sys.modules[__name__] = implementation
