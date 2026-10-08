"""Safe public errors for local deployment operations."""


class DemoError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise DemoError(message)
