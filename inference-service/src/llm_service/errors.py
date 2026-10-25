"""Fixed public errors; never forward native server messages or exception reprs."""

from llm_contract.generated.ttypes import LlmError


def failure(code: str, message: str, retryable: bool = False) -> LlmError:
    return LlmError(code=code, message=message, retryable=retryable)


class ExecutionError(Exception):
    """Mutable internal exception; generated Thrift exceptions are immutable.

    Python's async context managers attach tracebacks, so convert only at the RPC
    boundary rather than raising generated exceptions inside the HTTP adapter.
    """

    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)


class ExecutionUncertain(Exception):
    """Native exit could not be proved; never publish a terminal snapshot."""
