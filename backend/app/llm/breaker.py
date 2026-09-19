"""A small circuit breaker that keeps model failure out of the decision deadline."""

from collections import deque
from enum import Enum
import time


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, consecutive_failure_limit = 2, window_failure_limit = 3, window_size = 10, recovery_seconds = 30.0, clock = None):
        if consecutive_failure_limit < 1 or window_failure_limit < 1 or window_size < window_failure_limit or recovery_seconds < 0:
            raise ValueError("Invalid circuit breaker settings")
        self.consecutive_failure_limit = consecutive_failure_limit
        self.window_failure_limit = window_failure_limit
        self.recovery_seconds = recovery_seconds
        self._clock = clock or time.monotonic
        self._outcomes = deque(maxlen = window_size)
        self._consecutive_failures = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED

    @property
    def state(self):
        if self._state == CircuitState.OPEN and self._clock() - self._opened_at >= self.recovery_seconds:
            self._state = CircuitState.HALF_OPEN
        return self._state

    def allow_call(self):
        return self.state != CircuitState.OPEN

    def record_success(self):
        self._outcomes.append(True)
        self._consecutive_failures = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._outcomes.append(False)
        self._consecutive_failures += 1
        recent_failures = sum(not outcome for outcome in self._outcomes)
        if self._state == CircuitState.HALF_OPEN or self._consecutive_failures >= self.consecutive_failure_limit or recent_failures >= self.window_failure_limit:
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()

    def force_open(self):
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
