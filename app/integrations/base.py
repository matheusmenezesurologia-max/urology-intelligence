"""Shared HTTP client and normalized data types for external integrations.

Every outbound call goes through :class:`HttpClient`, which composes:
  * tenacity retry (exponential backoff + jitter)
  * a per-service circuit breaker
  * Prometheus request counters
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import httpx

from app.config import settings
from app.utils.circuit_breaker import CircuitBreaker
from app.utils.logger import get_logger
from app.utils.metrics import EXTERNAL_REQUESTS
from app.utils.retry import http_retry

log = get_logger("integrations")


@dataclass
class RawAuthor:
    name: str
    affiliation: Optional[str] = None
    orcid: Optional[str] = None


@dataclass
class RawArticle:
    """Source-agnostic article record produced by every fetch integration."""

    title: str
    source: str
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    abstract: Optional[str] = None
    journal: Optional[str] = None
    url: Optional[str] = None
    publication_date: Optional[date] = None
    authors: list[RawAuthor] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Stable identity used to deduplicate across sources."""
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        if self.pmid:
            return f"pmid:{self.pmid.strip()}"
        return f"title:{self.title.lower().strip()[:120]}"


class HttpClient:
    """A thin, resilient wrapper around ``httpx.Client``."""

    def __init__(self, service: str, base_url: str = "", headers: dict[str, str] | None = None) -> None:
        self.service = service
        self._breaker = CircuitBreaker(
            name=service,
            failure_threshold=5,
            recovery_timeout=60.0,
            expected_exceptions=(httpx.HTTPError,),
        )
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers or {},
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            follow_redirects=True,
        )

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @http_retry
    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        resp = self._client.request(method, url, **kwargs)
        EXTERNAL_REQUESTS.labels(service=self.service, status=str(resp.status_code)).inc()
        resp.raise_for_status()
        return resp

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self._breaker.call(self._request, "GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self._breaker.call(self._request, "POST", url, **kwargs)

    def get_json(self, url: str, **kwargs) -> Any:
        return self.get(url, **kwargs).json()
