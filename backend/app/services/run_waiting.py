"""Read fields derived from a run's parked question state."""

from datetime import timedelta
from typing import Any

from app.core.config import settings
from app.db.models.agent_run import AgentRun, RunStatus
from app.services.agent_runner import parked_question_items, parked_secret_request_items


def waiting_update(run: AgentRun) -> dict[str, Any]:
    """Describe what an answer-parked run is waiting for."""
    if run.status != RunStatus.AWAITING_ANSWER or run.ended_at is None:
        return {}
    questions = [
        question.question
        for parked in parked_question_items(run.paused_state)
        for question in parked.questions
    ]
    secret_requests = [item.secret_name for item in parked_secret_request_items(run.paused_state)]
    return {
        "waiting_questions": questions,
        "waiting_secret_requests": secret_requests,
        "waiting_expires_at": run.ended_at + timedelta(hours=settings.QUESTION_EXPIRY_HOURS),
    }
