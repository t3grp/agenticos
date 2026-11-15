"""Reviewed room handoffs; model output never admits execution by itself."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


class AgentPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    version_id: UUID


class LeadPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    enabled: bool = False
    lead: AgentPin | None = None
    specialists: list[AgentPin] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def valid_policy(self) -> LeadPolicyUpdate:
        if self.enabled and self.lead is None:
            raise ValueError("Choose a pinned lead before enabling routing")
        if len({pin.agent_id for pin in self.specialists}) != len(self.specialists):
            raise ValueError("Choose each specialist once")
        return self


class LeadPolicyRead(BaseModel):
    revision: int = 0
    enabled: bool = False
    lead: AgentPin | None = None
    specialists: list[AgentPin] = Field(default_factory=list)
    max_items: int = 3
    can_edit: bool = False


class HandoffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    expected_result: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=9000)
    ]
    specialist: AgentPin
    accountable_user_id: UUID
    reviewer_user_id: UUID
    due_at: AwareDatetime | None = None
    existing_task_id: UUID | None = None
    expected_task_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def task_revision(self) -> HandoffItem:
        if (self.existing_task_id is None) != (self.expected_task_revision is None):
            raise ValueError("An existing task requires its reviewed revision")
        return self


class RoutingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=20000)
    items: list[HandoffItem] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_items(self) -> RoutingOutput:
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("Handoff item ids must be unique")
        return self


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    item_ids: list[UUID] = Field(default_factory=list, max_length=3)


class RoutingProposalRead(BaseModel):
    id: UUID
    source_submission_id: UUID
    thread_root_message_id: UUID | None
    requester_id: UUID
    revision: int
    policy_revision: int
    status: Literal["pending", "accepted", "discarded"]
    items: list[HandoffItem]
    can_decide: bool
    can_accept: bool = False


class RoomTaskCard(BaseModel):
    reviewer_user_id: UUID
    task_id: UUID
    proposal_id: UUID
    item_id: UUID
    thread_root_message_id: UUID | None
    title: str
    status: Literal["planned", "in_progress", "waiting_for_user", "blocked", "done", "cancelled"]
    next_actor: Literal["user", "agent"]
    assignee_user_id: UUID | None
    assignee_agent_id: UUID | None
    revision: int


class RoomRoutingRead(BaseModel):
    policy: LeadPolicyRead
    proposals: list[RoutingProposalRead]
    tasks: list[RoomTaskCard]
