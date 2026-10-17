"""Agent 10 — Personalized Learning Engine.

Learns from persisted user feedback (opens, ignores, likes, dislikes, saves,
reading time, ratings) and adjusts per-topic relevance weights used by the
triage agent in subsequent runs. Fully persistent via the ``topic_weights`` and
``user_feedback`` tables.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.agents.common import audit
from app.database.models import TopicWeight, UserFeedback
from app.database.session import session_scope
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("personalized_learning")
AGENT = "personalized_learning"

LEARNING_RATE = 0.12
WEIGHT_MIN = 0.5
WEIGHT_MAX = 1.6


def _event_signal(event_type: str, read_seconds: int | None, rating: int | None) -> float:
    et = (event_type or "").lower()
    if et in {"liked", "saved"}:
        base = 1.0
    elif et == "opened":
        base = 0.3 + min((read_seconds or 0) / 180.0, 0.7)
    elif et == "disliked":
        base = -1.0
    elif et == "ignored":
        base = -0.4
    else:
        base = 0.0
    if rating is not None:
        # Ratings on a -1..+1 or 1..5 scale both map sensibly here.
        base += (rating - 3) / 2.0 if rating > 1 else float(rating)
    return max(-1.5, min(1.5, base))


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)

    with track_agent(AGENT):
        with session_scope() as session:
            feedback = session.scalars(
                select(UserFeedback).where(UserFeedback.topic.is_not(None))
            ).all()

            signals: dict[str, list[float]] = defaultdict(list)
            for fb in feedback:
                signals[fb.topic].append(_event_signal(fb.event_type, fb.read_seconds, fb.rating))

            adjustments: dict[str, float] = {}
            for topic, values in signals.items():
                avg = sum(values) / len(values)
                tw = session.scalar(select(TopicWeight).where(TopicWeight.topic == topic))
                if tw is None:
                    tw = TopicWeight(topic=topic, weight=1.0, observations=0)
                    session.add(tw)
                new_weight = tw.weight + LEARNING_RATE * avg
                tw.weight = round(max(WEIGHT_MIN, min(WEIGHT_MAX, new_weight)), 4)
                tw.observations += len(values)
                adjustments[topic] = tw.weight

            audit(session, run_id, AGENT, "learning_complete",
                  message=f"adjusted {len(adjustments)} topic weights from {len(feedback)} events",
                  payload={"adjustments": adjustments})

    log.info("personalized_learning_done", topics=len(adjustments), events=len(feedback))
    return {"stats": {"feedback_events": len(feedback), "topics_adjusted": len(adjustments)}}
