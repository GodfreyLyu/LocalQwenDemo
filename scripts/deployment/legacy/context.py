"""Scoped legacy operation context; helpers never import or create a CLI/target."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import ModuleType

_current: ContextVar[ModuleType] = ContextVar("legacy_deployment_context")


def api():
    try:
        return _current.get()
    except LookupError:
        raise RuntimeError("Legacy helpers require an explicit operation context") from None


@contextmanager
def using_context(context):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
