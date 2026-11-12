"""`AutoSkillLearningService` - turn an approved failure-learning proposal into a skill.

The failure-learning loop (#32) mines real failures, curates them into ledger
entries and proposes rule text; a person applies the proposal. This service is
the step after that approval, and only that step: it takes a skill-target
proposal a person already approved and materialises it in the skill catalog,
so the same failure does not repeat. It mines nothing, proposes nothing, and
writes skills only through `SkillService` — there is no second skill writer
here, and no second mining pipeline or proposal flow.

What #32 already does, and what this adds. #32's apply edits an existing
skill live through `SkillService.update`, which bumps the skill's
informational `version`. What it cannot express is a proposal that *is* a new
skill: its create step requires a skill id for a skill target. So this
service has two cases:

- The proposal names a skill. When the skill already carries the approved
  text, learning is a no-op: the call writes nothing and reports
  `already_learned`, so learning the same proposal twice is safe. When the
  skill still carries the text the proposal was written against (its SHA-256
  matches `base_content_sha256`), the service applies the learned text
  itself, through the same `SkillService.update` #32 uses. When it carries
  neither, somebody rewrote the skill since the proposal was written, and
  learning refuses rather than guessing — re-base the proposal instead.
- The proposal names no skill. The service creates one through
  `SkillService.create`, in the `learned` category so learned skills are
  findable in the listing, with the name and description taken from the
  request or derived from the pattern and rationale — a #32 proposal carries
  neither field, and that gap is the adapter this service is: if #32 later
  records a name and description on its proposals, the derivation goes away
  and the request fields carry them instead. A skill of the derived name
  already carrying the same text is the same no-op as above; one carrying
  different text is a name collision, refused as `AlreadyExistsError`.

Provenance. Every catalog write this service performs is audited as
`skill.learned`, with the proposal id, the ledger entry and pattern key that
motivated it, and the content hashes before and after. A no-op writes no
audit entry: the approval itself was already audited when #32 applied the
proposal. There is no version history and no rollback — `Skill.version` is
the informational bump `SkillService` already maintains, and a bad learn is
undone by a new edit, not a restore, exactly as #32 documents for its own
skill applies.

No migration: learned skills are ordinary `skills` rows, and their provenance
is the audit trail. Reads resolve the agent with `AGENTS_VIEW` and the skill
with `SKILLS_EDIT`, the same split #32 uses — a refusal answers as "not
found".
"""

import hashlib
import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.exceptions import AlreadyExistsError, ConflictError
from app.core.permissions import AuthContext, Perm
from app.db.models.skill import Skill
from app.repositories import skill as skill_repo
from app.schemas.auto_skill_learning import LearnedSkillRead, LearnSkillCreate
from app.schemas.skill import SkillUpdate
from app.services.agent_registry import AgentRegistryService
from app.services.skills import SkillService

LEARNED_CATEGORY = "learned"
"""The category a created learned skill is filed under.

The listing filters by category, so this is how a learned skill is found
again without a new column, table or migration — and how a reader tells a
skill the loop wrote from one a person wrote.
"""

_FALLBACK_NAME = "learned-skill"
_FALLBACK_DESCRIPTION = "Learned from a recurring failure. See the audit trail for its proposal."


def _sha256(content: str) -> str:
    """The content hash, the same digest #32 records as its base."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    """A name-safe slug: lowercase, hyphen-joined, no leading or trailing hyphen."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or _FALLBACK_NAME


class AutoSkillLearningService:
    """Materialise approved skill-target proposals as catalog skills."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.registry = AgentRegistryService(db)
        self.skills = SkillService(db)

    async def learn(
        self, ctx: AuthContext, agent_id: UUID, data: LearnSkillCreate
    ) -> LearnedSkillRead:
        """Learn one approved proposal, creating or updating the skill.

        The caller asserts the proposal was human-approved: until #32 lands,
        this service cannot read its proposal row to check the status itself,
        so the approval is the caller's to enforce and the audit entry's to
        show. See the module docstring for the two cases and the refusals.

        Raises:
            NotFoundError: The agent or the named skill is out of reach.
            ConflictError: The named skill changed since the proposal was
                written — its content matches neither the approved text nor
                the base the proposal was written against.
            AlreadyExistsError: A different skill already owns the derived
                name with different content.
        """
        await self.registry.get(ctx, agent_id, perm=Perm.AGENTS_VIEW)
        new_hash = _sha256(data.content)
        if data.skill_id is not None:
            skill = await self.skills.get(ctx, data.skill_id, perm=Perm.SKILLS_EDIT)
            return await self._learn_into_existing(ctx, skill, data, new_hash)
        return await self._learn_as_new(ctx, data, new_hash)

    async def _learn_into_existing(
        self, ctx: AuthContext, skill: Skill, data: LearnSkillCreate, new_hash: str
    ) -> LearnedSkillRead:
        """The proposal edits a skill that already exists."""
        current_hash = _sha256(skill.content)
        if current_hash == new_hash:
            return self._read(skill, data, created=False, already_learned=True)
        if data.base_content_sha256 is not None and current_hash == data.base_content_sha256:
            updated = await self.skills.update(ctx, skill.id, SkillUpdate(content=data.content))
            await self._audit(ctx, updated, data, new_hash, created=False)
            return self._read(updated, data, created=False, already_learned=False)
        raise ConflictError(
            message="The skill has changed since this proposal was written - "
            "re-base the proposal instead",
            details={
                "proposal_id": str(data.proposal_id),
                "skill_id": str(skill.id),
                "base_content_sha256": data.base_content_sha256,
            },
        )

    async def _learn_as_new(
        self, ctx: AuthContext, data: LearnSkillCreate, new_hash: str
    ) -> LearnedSkillRead:
        """The proposal is a new skill: create it, or recognise it as learned."""
        name = data.name or _derive_name(data)
        existing = await skill_repo.get_by_name(self.db, name, organization_id=ctx.organization_id)
        if existing is not None:
            if _sha256(existing.content) == new_hash:
                return self._read(existing, data, created=False, already_learned=True)
            raise AlreadyExistsError(
                message=(
                    f"A skill named '{name}' already exists with different content. "
                    "The name is how a model refers to a skill and cannot be changed "
                    "afterwards, so choose a different one - or open the existing skill "
                    "and edit it, which reaches every agent bound to it."
                ),
                details={"name": name},
            )
        skill = await self.skills.create(
            ctx,
            name=name,
            description=data.description or _derive_description(data),
            content=data.content,
            category=LEARNED_CATEGORY,
        )
        await self._audit(ctx, skill, data, new_hash, created=True)
        return self._read(skill, data, created=True, already_learned=False)

    async def _audit(
        self,
        ctx: AuthContext,
        skill: Skill,
        data: LearnSkillCreate,
        new_hash: str,
        *,
        created: bool,
    ) -> None:
        """The provenance record: which proposal, which failures, which text."""
        await record_audit(
            self.db,
            actor_user_id=ctx.subject_id,
            organization_id=ctx.organization_id,
            action="skill.learned",
            target_type="skill",
            target_id=str(skill.id),
            details={
                "proposal_id": str(data.proposal_id),
                "ledger_entry_id": str(data.ledger_entry_id)
                if data.ledger_entry_id is not None
                else None,
                "pattern_key": data.pattern_key,
                "created": created,
                "version": skill.version,
                "content_sha256": new_hash,
                "base_content_sha256": data.base_content_sha256,
                "rationale": data.rationale,
            },
        )

    @staticmethod
    def _read(
        skill: Skill, data: LearnSkillCreate, *, created: bool, already_learned: bool
    ) -> LearnedSkillRead:
        return LearnedSkillRead(
            skill_id=skill.id,
            name=skill.name,
            version=skill.version,
            content_sha256=_sha256(skill.content),
            created=created,
            already_learned=already_learned,
            proposal_id=data.proposal_id,
            ledger_entry_id=data.ledger_entry_id,
            pattern_key=data.pattern_key,
        )


def _derive_name(data: LearnSkillCreate) -> str:
    """A catalog name for a proposal that carries none.

    The pattern key is the most stable thing a proposal has — `failed:...`
    slugifies to `learned-failed-...` — and the rationale's first words are
    the fallback when there is no pattern. Either way the name is only a
    default: the request may name the skill explicitly, and a collision with
    different content is refused rather than renamed silently.
    """
    if data.pattern_key:
        return f"learned-{_slug(data.pattern_key)}"[:64].rstrip("-") or _FALLBACK_NAME
    if data.rationale:
        return _slug(" ".join(data.rationale.split()[:8]))[:64].rstrip("-") or _FALLBACK_NAME
    return _FALLBACK_NAME


def _derive_description(data: LearnSkillCreate) -> str:
    """A catalog description for a proposal that carries none.

    The rationale, in full when it fits the 500-character column; otherwise
    the pattern key, so the skill still says which failure taught it.
    """
    if data.rationale:
        return data.rationale[:500]
    if data.pattern_key:
        return f"Learned from recurring failures: {data.pattern_key}"[:500]
    return _FALLBACK_DESCRIPTION
