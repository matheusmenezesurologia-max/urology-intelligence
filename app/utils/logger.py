"""Structured logging via structlog.

Emits JSON logs in production (machine-parseable for Loki/ELK) and pretty,
colorized logs in development.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.app_env == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, configuring logging on first use."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)


def bind_run(run_id: str, agent: str | None = None) -> None:
    """Bind run-scoped context vars so every subsequent log line is correlated."""
    structlog.contextvars.bind_contextvars(run_id=run_id)
    if agent:
        structlog.contextvars.bind_contextvars(agent=agent)
