"""A minimal, thread-safe circuit breaker.

States:
    CLOSED     -> calls pass through; failures are counted.
    OPEN       -> calls fail fast until ``recovery_timeout`` elapses.
    HALF_OPEN  -> a single trial call is allowed; success closes, failure re-opens.
"""
from __future__ import annotations

import functools
import threading
import time
from enum import Enum
from typing import Callable

from app.utils.logger import get_logger
from app.utils.metrics import CIRCUIT_STATE

log = get_logger("circuit_breaker")


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_STATE_CODE = {State.CLOSED: 0, State.OPEN: 1, State.HALF_OPEN: 2}


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exceptions: tuple[type[Exception], ...] = (Exception,),
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.RLock()
        CIRCUIT_STATE.labels(name=name).set(_STATE_CODE[State.CLOSED])

    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, state: State) -> None:
        self._state = state
        CIRCUIT_STATE.labels(name=self.name).set(_STATE_CODE[state])

    def _allow(self) -> bool:
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self.recovery_timeout:
                    self._set_state(State.HALF_OPEN)
                    log.info("circuit_half_open", name=self.name)
                    return True
                return False
            return True

    def _on_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state != State.CLOSED:
                self._set_state(State.CLOSED)
                log.info("circuit_closed", name=self.name)

    def _on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == State.HALF_OPEN or self._failures >= self.failure_threshold:
                self._set_state(State.OPEN)
                self._opened_at = time.monotonic()
                log.warning("circuit_open", name=self.name, failures=self._failures)

    def call(self, fn: Callable, *args, **kwargs):
        if not self._allow():
            raise CircuitOpenError(f"circuit '{self.name}' is open")
        try:
            result = fn(*args, **kwargs)
        except self.expected_exceptions:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def __call__(self, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return self.call(fn, *args, **kwargs)

        return wrapper
