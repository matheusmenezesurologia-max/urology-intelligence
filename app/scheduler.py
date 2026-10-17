"""APScheduler setup for the weekly pipeline (Friday 18:00 America/Sao_Paulo)."""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.utils.logger import get_logger
from app.workflows.weekly_pipeline import run_pipeline

log = get_logger("scheduler")

try:  # Python 3.12 stdlib; APScheduler accepts a tzinfo object.
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(settings.timezone)
except Exception:  # pragma: no cover - fallback to string
    _TZ = settings.timezone  # type: ignore[assignment]

_scheduler: BackgroundScheduler | None = None
JOB_ID = "weekly_pipeline"


def _job() -> None:
    log.info("scheduled_run_triggered")
    try:
        run_pipeline()
    except Exception as exc:  # scheduler thread must survive a failed run
        log.error("scheduled_run_failed", error=str(exc))


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=_TZ)
    return _scheduler


def start_scheduler() -> BackgroundScheduler:
    """Register the weekly cron job and start the scheduler."""
    scheduler = get_scheduler()
    trigger = CronTrigger(
        day_of_week=settings.pipeline_cron_day_of_week,
        hour=settings.pipeline_cron_hour,
        minute=settings.pipeline_cron_minute,
        timezone=_TZ,
    )
    scheduler.add_job(
        _job,
        trigger=trigger,
        id=JOB_ID,
        name="Weekly Urology Intelligence pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    if not scheduler.running:
        scheduler.start()
    next_run = scheduler.get_job(JOB_ID).next_run_time
    log.info("scheduler_started", next_run_time=str(next_run),
             cron=f"{settings.pipeline_cron_day_of_week} {settings.pipeline_cron_hour}:{settings.pipeline_cron_minute:02d}",
             timezone=settings.timezone)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None
