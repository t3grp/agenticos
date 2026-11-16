"""Redacted browser teaching contracts. Request models never accept recorder events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema


class BrowserTeachingStart(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    call_id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=64)


class BrowserTeachingStep(BaseSchema):
    event_id: UUID
    sequence: int = Field(ge=0)
    action: Literal["click", "input_parameter", "navigate", "scroll", "checkpoint"]
    origin: str
    target_role: str | None = None
    target_fingerprint: str | None = None
    parameter: str | None = None
    reviewed_label: str | None = Field(default=None, max_length=64)


class BrowserTeachingParameter(BaseSchema):
    event_id: UUID
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=64)


class BrowserTeachingStepReview(BaseSchema):
    event_id: UUID
    reviewed_label: str = Field(min_length=1, max_length=64)


class BrowserTeachingReview(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    steps: list[BrowserTeachingStepReview] = Field(max_length=500)
    parameters: list[BrowserTeachingParameter] = Field(max_length=32)


class BrowserTeachingRead(BaseSchema):
    id: UUID
    agent_id: UUID
    run_id: UUID
    call_id: str
    lease_id: str
    origin: str
    status: str
    pause_reason: str | None
    revision: int
    steps: list[BrowserTeachingStep]
    parameters: list[BrowserTeachingParameter]
    proposal_id: UUID | None
    expires_at: datetime


class BrowserTeachingAction(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume"]
    revision: int = Field(ge=1)
