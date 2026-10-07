"""Model contract shared by the Ollama adapter and injected test doubles."""

import threading
from typing import Protocol


class ReviewModel(Protocol):
    def load(self) -> None: ...
    def count_tokens(self, source: str, language: str) -> int | None: ...
    def review(self, source: str, language: str, stop: threading.Event) -> str: ...
