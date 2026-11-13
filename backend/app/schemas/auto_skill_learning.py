"""Schemas for automatic skill learning (feature #9).

The request carries one human-approved skill-target proposal from the
failure-learning loop (#32) — the proposal's own fields, not a reference the
server can resolve yet: #32's proposal table is not on `t3`, so until it
lands the caller (the console, or #32's apply step once wired) supplies the
approved text and its provenance. The service never mines failures and never
proposes: it only materialises what a person already approved.
"""

from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class LearnSkillCreate(BaseSchema):
    """Learn one approved skill-target proposal into the skill catalog.

    `skill_id` names the skill the proposal edits, or is absent for a
    proposal that *is* a new skill — the case #32 cannot express, because its
    create step requires a skill id for a skill target. `name` and
    `description` are the new skill's catalog fields; when they are absent
    the service derives them from `pattern_key` and `rationale` (see
    `AutoSkillLearningService`), because a #32 proposal carries neither.
    `base_content_sha256` is the SHA-256 #32 recorded when the proposal was
    written; it is the guard that refuses to learn over a skill somebody has
    since rewritten.
    """

    proposal_id: UUID = Field(description="The #32 proposal this learning comes from")
    skill_id: UUID | None = Field(
        default=None,
        description="The skill the proposal edits, or absent when the proposal is a new skill",
    )
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    content: str = Field(
        min_length=1,
        description="The learned text — the proposal's `new_text`, in full",
    )
    rationale: str | None = Field(
        default=None, description="Why the proposal was written, for the audit trail"
    )
    ledger_entry_id: UUID | None = Field(
        default=None, description="The #32 ledger entry whose pattern motivated the proposal"
    )
    pattern_key: str | None = Field(
        default=None,
        max_length=120,
        description="The mined pattern key, e.g. `failed:ModelHTTPError:400`",
    )
    base_content_sha256: str | None = Field(
        default=None,
        max_length=64,
        description="SHA-256 of the skill content the proposal was written against",
    )


class LearnedSkillRead(BaseSchema):
    """The skill as learned, and where it came from."""

    skill_id: UUID
    name: str
    version: int = Field(description="The catalog version after learning — see `Skill.version`")
    content_sha256: str = Field(description="SHA-256 of the learned content, as stored")
    created: bool = Field(
        description="True when learning created the skill rather than updating one"
    )
    already_learned: bool = Field(
        description=(
            "True when the skill already carried exactly this text: the call "
            "wrote nothing, so learning the same approved proposal twice is safe"
        )
    )
    proposal_id: UUID
    ledger_entry_id: UUID | None = None
    pattern_key: str | None = None
