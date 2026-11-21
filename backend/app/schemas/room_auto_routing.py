"""Bounded policy, private ledger rows, and safe room decision receipts."""

import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import BaseSchema


class RoomAutoRoutingPolicyRead(BaseSchema):
    enabled: bool = False
    revision: int = Field(ge=0)
    can_edit: bool = False
    router_agent_id: UUID | None = None
    router_version_id: UUID | None = None
    model_profile_id: UUID | None = None
    quiet_window_ms: int = Field(default=2500, ge=0, le=10000)
    max_responders: int = Field(default=1, ge=1, le=4)


class RoomAutoRoutingPolicyWrite(BaseSchema):
    expected_revision: int = Field(ge=0)
    enabled: bool = False
    router_agent_id: UUID | None = None
    model_profile_id: UUID | None = None
    quiet_window_ms: int = Field(default=2500, ge=0, le=10000)
    max_responders: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def require_router_and_profile_together(self) -> RoomAutoRoutingPolicyWrite:
        configured = self.router_agent_id is not None and self.model_profile_id is not None
        if (self.router_agent_id is None) != (self.model_profile_id is None) or (
            self.enabled and not configured
        ):
            raise ValueError("Choose both a room router and an organization model profile")
        return self


class RoomAutoRoutingCandidateSnapshot(BaseModel):
    """Only the bounded public description the classifier needs for one candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    version_id: UUID
    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=600)


class RoomAutoRoutingScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    score: float = Field(ge=0, le=1)

    @field_validator("score")
    @classmethod
    def score_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Score must be finite")
        return value


class RoomAutoRoutingSocialIntent(BaseModel):
    """One optional emoji reaction intent; target and actor remain server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    react: bool
    emoji: str = Field(default="", max_length=16)


class RoomAutoRoutingDecisionOutput(BaseModel):
    """The complete bounded classifier output; it cannot grant new candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_ids: list[UUID] = Field(default_factory=list, max_length=4)
    scores: list[RoomAutoRoutingScore] = Field(default_factory=list, max_length=100)
    needs_agent: bool = True
    already_answered: bool = False
    for_everyone: bool = False
    addressee_id: UUID | None = None
    social_intent: RoomAutoRoutingSocialIntent | None = None
    reason: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def ids_are_unique(self) -> RoomAutoRoutingDecisionOutput:
        if len(self.selected_ids) != len(set(self.selected_ids)):
            raise ValueError("Selected agents must be unique")
        score_ids = [item.agent_id for item in self.scores]
        if len(score_ids) != len(set(score_ids)):
            raise ValueError("Each agent may have one routing score")
        return self


class RoomAutoRoutingDecisionCreate(BaseModel):
    """Frozen acceptance data passed from RoomSubmission into the routing ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    conversation_id: UUID
    submission_id: UUID
    accepted_message_id: UUID
    requester_id: UUID
    thread_root_message_id: UUID | None = None
    accepted_at: datetime
    ready_at: datetime
    policy_revision: int = Field(ge=1)
    router_agent_id: UUID
    router_name: str = Field(min_length=1, max_length=120)
    router_version_id: UUID
    model_profile_id: UUID
    model_profile_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    quiet_window_ms: int = Field(ge=0, le=10000)
    max_responders: int = Field(ge=1, le=4)
    candidate_snapshot: list[RoomAutoRoutingCandidateSnapshot] = Field(max_length=100)
    grouped_message_ids: list[UUID] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def accepted_message_is_in_group(self) -> RoomAutoRoutingDecisionCreate:
        if self.accepted_message_id not in self.grouped_message_ids:
            raise ValueError("The accepted message must be in the routed group")
        if len(self.grouped_message_ids) != len(set(self.grouped_message_ids)):
            raise ValueError("Grouped message ids must be unique")
        candidate_ids = [item.agent_id for item in self.candidate_snapshot]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Candidate agent ids must be unique")
        return self


RoomAutoRoutingTerminalStatus = Literal["selected", "abstained", "failed", "unknown"]
RoomAutoRoutingPublicStatus = Literal[
    "pending", "running", "selected", "abstained", "failed", "unknown"
]


class RoomAutoRoutingSelectedAgentRead(BaseModel):
    """A selected same-organization candidate, named from its accepted snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    name: str = Field(min_length=1, max_length=120)


class RoomAutoRoutingDecisionRead(BaseModel):
    """Room-safe receipt fields; private prompt, reason, and provider data stay internal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted_message_id: UUID
    grouped_message_ids: list[UUID] = Field(min_length=1, max_length=8)
    status: RoomAutoRoutingPublicStatus
    router_agent_id: UUID
    router_name: str = Field(min_length=1, max_length=120)
    selected_agents: list[RoomAutoRoutingSelectedAgentRead] = Field(
        default_factory=list, max_length=4
    )
    accepted_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def accepted_message_is_in_group(self) -> RoomAutoRoutingDecisionRead:
        if self.accepted_message_id not in self.grouped_message_ids:
            raise ValueError("The accepted message must be in the routed group")
        if len(self.grouped_message_ids) != len(set(self.grouped_message_ids)):
            raise ValueError("Grouped message ids must be unique")
        if self.status != "selected" and self.selected_agents:
            raise ValueError("Only a selected decision may expose selected agents")
        return self


class RoomAutoRoutingDecisionListRead(BaseModel):
    """One bounded read for IDs on an already-authorized room transcript page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[RoomAutoRoutingDecisionRead] = Field(max_length=100)


class RoomAutoRoutingDecisionReceipt(BaseModel):
    """Internal typed receipt used by the repository; never returned to room users."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RoomAutoRoutingTerminalStatus
    grouped_message_ids: list[UUID] = Field(min_length=1, max_length=8)
    decision_output: RoomAutoRoutingDecisionOutput | None = None
    router_run_id: UUID | None = None
    error_code: str | None = Field(default=None, max_length=64)
