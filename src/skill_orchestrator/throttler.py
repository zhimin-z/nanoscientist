"""Execution throttler for managing concurrent node execution."""

import asyncio
import random
from typing import Awaitable, Callable, Optional, TypeVar

from config import get_config
from skill_orchestrator.models import NodeExecutionResult, NodeStatus, NodeFailureReason

T = TypeVar("T")


class RateLimitError(Exception):
    """Raised when API rate limit is exceeded."""

    pass


class ExecutionThrottler:
    """Manages concurrent execution limits with adaptive backoff.

    Features:
    - Semaphore-based concurrency control
    - Exponential backoff with jitter for rate limit handling
    - Adaptive delay multiplier that adjusts based on success/failure
    """

    def __init__(
        self,
        max_concurrent: Optional[int] = None,
        retry_base_delay: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        """Initialize the throttler.

        Args:
            max_concurrent: Maximum number of concurrent executions (from config if not specified)
            retry_base_delay: Base delay in seconds for retries (from config if not specified)
            max_retries: Maximum number of retry attempts (from config if not specified)
        """
        cfg = get_config()
        _max_concurrent = max_concurrent if max_concurrent is not None else cfg.max_concurrent
        _retry_base_delay = retry_base_delay if retry_base_delay is not None else cfg.retry_base_delay
        _max_retries = max_retries if max_retries is not None else cfg.max_retries

        self.semaphore = asyncio.Semaphore(_max_concurrent)
        self.retry_base_delay = _retry_base_delay
        self.max_retries = _max_retries
        self._current_delay_multiplier = 1.0
        self._lock = asyncio.Lock()

    async def execute_with_throttle(
        self,
        coro_factory: Callable[[], Awaitable[NodeExecutionResult]],
        node_id: str,
    ) -> NodeExecutionResult:
        """Execute a coroutine with rate limiting and retry.

        Args:
            coro_factory: A callable that returns a new coroutine each time
            node_id: The node ID for error reporting

        Returns:
            NodeExecutionResult from the execution
        """
        async with self.semaphore:
            for attempt in range(self.max_retries):
                try:
                    # Create a fresh coroutine for each attempt
                    result = await coro_factory()

                    # Success - gradually reduce delay multiplier
                    async with self._lock:
                        self._current_delay_multiplier = max(
                            1.0, self._current_delay_multiplier * 0.8
                        )

                    return result

                except RateLimitError:
                    if attempt == self.max_retries - 1:
                        # Last attempt failed
                        break

                    # Calculate delay with exponential backoff and jitter
                    async with self._lock:
                        delay = (
                            self.retry_base_delay
                            * self._current_delay_multiplier
                            * (2**attempt)
                        )
                        delay += random.uniform(0, delay * 0.1)  # 10% jitter
                        self._current_delay_multiplier *= 1.5

                    await asyncio.sleep(delay)

            # All retries exhausted
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error="Rate limit exceeded after retries",
                failure_reason=NodeFailureReason.RATE_LIMIT,
            )

    async def execute_batch(
        self,
        tasks: list[tuple[Callable[[], Awaitable[NodeExecutionResult]], str]],
    ) -> list[NodeExecutionResult]:
        """Execute multiple tasks concurrently with throttling.

        Args:
            tasks: List of (coro_factory, node_id) tuples

        Returns:
            List of NodeExecutionResult, one per task
        """
        coros = [
            self.execute_with_throttle(coro_factory, node_id)
            for coro_factory, node_id in tasks
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        # Convert exceptions to NodeExecutionResult
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                _, node_id = tasks[i]
                processed.append(
                    NodeExecutionResult(
                        node_id=node_id,
                        status=NodeStatus.FAILED,
                        error=str(result),
                        failure_reason=NodeFailureReason.UNKNOWN,
                    )
                )
            else:
                processed.append(result)

        return processed
