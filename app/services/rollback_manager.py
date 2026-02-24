from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger()


class RollbackManager:
    """Registers compensating actions and executes them in reverse order on rollback."""

    def __init__(self) -> None:
        self._stack: list[Callable[[], Coroutine[Any, Any, None]]] = []

    def register(self, fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register an async compensating action to run on rollback."""
        self._stack.append(fn)

    async def rollback(self) -> None:
        """Execute all compensating actions in reverse registration order."""
        for fn in reversed(self._stack):
            try:
                await fn()
            except Exception as exc:
                logger.error("rollback_action_failed", error=str(exc), fn=getattr(fn, "__name__", repr(fn)))
