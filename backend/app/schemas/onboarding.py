"""Schemas for guided onboarding team creation."""

from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class OnboardingProfileRequest(BaseSchema):
    role: str = Field(default="general", description="User's primary role or title")
    industry: str = Field(
        default="general", description="Industry key, matching catalog or general"
    )
    team_size: str = Field(
        default="small", description="Team size description, e.g. solo, small, company"
    )
    goals: list[str] = Field(default_factory=list, description="Primary goals or pain points")
    details: str | None = Field(default=None, description="Optional extra details on workflows")
    tools: list[str] = Field(default_factory=list, description="Tools or software used")


class ProposedSpecialist(BaseSchema):
    key: str | None = Field(default=None, description="Template key if based on a catalog template")
    name: str = Field(description="Name of the specialist agent")
    role: str = Field(description="Role or specialty")
    description: str = Field(description="Description of what this agent does")
    capabilities: list[str] = Field(default_factory=list, description="Key capabilities")
    recommended: bool = Field(default=True, description="Whether selected by default")


class ProposedLead(BaseSchema):
    name: str = Field(description="Name of the lead orchestrator")
    role: str = Field(description="Lead role title")
    description: str = Field(description="Description of how the lead coordinates the team")
    starters: list[str] = Field(default_factory=list, description="Suggested starter prompts")


class OnboardingTeamProposal(BaseSchema):
    industry: str
    role: str
    rationale: str
    lead: ProposedLead
    specialists: list[ProposedSpecialist]


class CreateOnboardingTeamRequest(BaseSchema):
    lead: ProposedLead
    specialists: list[ProposedSpecialist] = Field(default_factory=list)
    project_name: str | None = None


class CreatedAgentInfo(BaseSchema):
    id: UUID
    name: str
    slug: str
    role: str
    is_lead: bool
    status: str


class OnboardingTeamResult(BaseSchema):
    lead_agent_id: UUID
    lead_agent_slug: str
    lead_conversation_id: UUID | None = None
    created_agents: list[CreatedAgentInfo] = Field(default_factory=list)
    message: str
