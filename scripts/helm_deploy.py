"""Compatibility entry; implementation lives in deployment.legacy.helm_deploy."""

import sys

import deployment.legacy.helm_deploy as implementation

if __name__ == "__main__":
    implementation.entry()
else:
    sys.modules[__name__] = implementation
