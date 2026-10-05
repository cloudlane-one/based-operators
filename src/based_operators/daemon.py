"""Explicit fresh-model access for Kopf daemon handlers."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from based_operators.validation import InvalidDesiredInputError, validate_resource


class ResourceContext:
    """Refresh a typed snapshot from Kopf's latest observed live body."""

    def __init__(self, model: type[BaseModel], body: Mapping[str, Any], stopped: Any) -> None:
        """Store the live Kopf body and its cancellation-aware stop flag."""
        self.model = model
        self.body = body
        self.stopped = stopped

    def refresh(self) -> BaseModel:
        """Return a newly validated detached snapshot of the current body."""
        return validate_resource(self.model, dict(self.body))

    def wait(self, delay: float = 1.0) -> BaseModel | None:
        """Wait for valid desired input or stop (for synchronous daemons)."""
        while not self.stopped:
            try:
                return self.refresh()
            except InvalidDesiredInputError:
                self.stopped.wait(delay)
        return None

    async def wait_async(self, delay: float = 1.0) -> BaseModel | None:
        """Wait for valid desired input or stop (for asynchronous daemons)."""
        while not self.stopped:
            try:
                return self.refresh()
            except InvalidDesiredInputError:
                await self.stopped.wait(delay)
        return None
