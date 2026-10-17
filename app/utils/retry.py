"""Reusable retry policies built on tenacity."""
from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from app.config import settings
from app.utils.logger import get_logger

log = get_logger("retry")

# Transient transport errors that are always worth retrying.
TRANSIENT_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient transport errors and 5xx/429 responses, but not other 4xx."""
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


def _log_retry(retry_state) -> None:  # pragma: no cover - logging side effect
    log.warning(
        "retrying",
        attempt=retry_state.attempt_number,
        wait=getattr(retry_state.next_action, "sleep", None),
        fn=getattr(retry_state.fn, "__name__", "?"),
    )


def http_retry(fn):
    """Decorator: exponential backoff + jitter for transient/5xx/429 HTTP failures."""
    wrapped = retry(
        reraise=True,
        stop=stop_after_attempt(settings.http_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=20) + wait_random(0, 1.5),
        retry=retry_if_exception(_is_retryable),
        before_sleep=_log_retry,
    )
    return wrapped(fn)


def _is_retryable_llm(exc: BaseException) -> bool:
    """Retry LLM calls only on rate limits (429), 5xx, and connection/timeout errors.

    Client errors like 400 (bad request), 401/403 (auth), and 404 (unknown model)
    are permanent — retrying them just wastes calls and time.
    """
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return "connection" in name or "timeout" in name


def llm_retry(fn):
    """Decorator: retry policy tuned for LLM provider calls (429/5xx/connection only)."""
    wrapped = retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60) + wait_random(0, 2),
        retry=retry_if_exception(_is_retryable_llm),
        before_sleep=_log_retry,
    )
    return wrapped(fn)
