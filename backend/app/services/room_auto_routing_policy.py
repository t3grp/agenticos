"""Manage opt-in automatic model routing for an individual conversation room."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, AppException, AuthorizationError
from app.core.field_errors import refused_field
from app.core.permissions import AuthContext, Perm
from app.db.locks import LockScope, hold_subject
from app.db.models.agent import AgentKind
from app.db.models.conversation import Conversation
from app.db.models.room_auto_routing import RoomAutoRoutingPolicy
from app.repositories import project as project_repo
from app.repositories import room_auto_routing as routing_repo
from app.schemas.room_auto_routing import (
    RoomAutoRoutingPolicyRead,
    RoomAutoRoutingPolicyWrite,
)
from app.services.access import PROJECT, resolve_access
from app.services.agent_registry import AgentRegistryService
from app.services.model_outputs import model_outputs
from app.services.model_profile import ModelProfileService
from app.services.room_participant import RoomParticipantService
from app.services.room_submission import RoomSubmissionService, cloud_agent, signed_in


class RoomAutoRoutingPolicyService:
    """Read and update one room's explicit automatic-routing policy."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.rooms = RoomSubmissionService(db)
        self.participants = RoomParticipantService(db)
        self.registry = AgentRegistryService(db)
        self.models = ModelProfileService(db)

    async def _can_edit(self, ctx: AuthContext, room: Conversation) -> bool:
        """Use project edit for project rooms and existing room-manager rules otherwise."""
        if room.is_archived:
            return False
        if room.project_id is None:
            return await self.participants._can_manage(ctx, room)
        project = await project_repo.get(
            self.db, room.project_id, organization_id=ctx.organization_id
        )
        return project is not None and await resolve_access(
            self.db, ctx, project, Perm.PROJECTS_EDIT, resource_type=PROJECT
        )

    @staticmethod
    def _read(row: RoomAutoRoutingPolicy | None, *, can_edit: bool) -> RoomAutoRoutingPolicyRead:
        if row is None:
            return RoomAutoRoutingPolicyRead(revision=0, can_edit=can_edit)
        return RoomAutoRoutingPolicyRead(
            enabled=row.enabled,
            revision=row.revision,
            can_edit=can_edit,
            router_agent_id=row.router_agent_id,
            router_version_id=row.router_version_id,
            model_profile_id=row.model_profile_id,
            quiet_window_ms=row.quiet_window_ms,
            max_responders=row.max_responders,
        )

    async def policy(self, ctx: AuthContext, conversation_id: UUID) -> RoomAutoRoutingPolicyRead:
        """Return persisted settings or disabled defaults, including current edit authority."""
        signed_in(ctx)
        room = await self.rooms.room(ctx, conversation_id)
        row = await routing_repo.policy(self.db, ctx.organization_id, conversation_id)
        return self._read(row, can_edit=await self._can_edit(ctx, room))

    async def _router_version(self, ctx: AuthContext, room: Conversation, agent_id: UUID) -> UUID:
        if not await routing_repo.router_is_on_roster(
            self.db,
            organization_id=ctx.organization_id,
            conversation_id=room.id,
            agent_id=agent_id,
        ):
            raise refused_field("router_agent_id", "Choose a bot on this room's roster")
        try:
            agent, spec, version_id = await self.registry.get_runnable_spec(ctx, agent_id)
            if agent.kind != AgentKind.AGENT:
                raise refused_field("router_agent_id", "Choose an ordinary published room bot")
            cloud_agent(spec)
        except AppException:
            raise refused_field(
                "router_agent_id", "Choose an available, cloud-runnable published room bot"
            ) from None
        return version_id

    async def _validate_model_profile(self, ctx: AuthContext, profile_id: UUID) -> None:
        try:
            spec = await self.models.resolve_for_organization(
                ctx.organization_id, profile_id=profile_id
            )
        except AppException:
            raise refused_field(
                "model_profile_id", "Choose a usable organization-owned chat model profile"
            ) from None
        if not model_outputs(spec.provider, spec.model, base_url=spec.credential.base_url).chat:
            raise refused_field("model_profile_id", "Choose a model profile that supports chat")

    async def update_policy(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        data: RoomAutoRoutingPolicyWrite,
    ) -> RoomAutoRoutingPolicyRead:
        """Replace the policy under the room turn lock with revision-based concurrency."""
        signed_in(ctx)
        room = await self.rooms.room(ctx, conversation_id, for_write=True)
        if not await self._can_edit(ctx, room):
            raise AuthorizationError(message="Only a room manager may edit automatic routing")

        await hold_subject(self.db, LockScope.CONVERSATION_TURN, conversation_id)
        # Re-read tenant, archive and manager authority after acquiring the lock.
        room = await self.rooms.room(ctx, conversation_id, for_write=True)
        if not await self._can_edit(ctx, room):
            raise AuthorizationError(message="Only a room manager may edit automatic routing")
        row = await routing_repo.policy(self.db, ctx.organization_id, conversation_id, lock=True)
        current_revision = 0 if row is None else row.revision
        if data.expected_revision != current_revision:
            raise AlreadyExistsError(
                message="The automatic routing policy changed; review its current revision"
            )

        version_id: UUID | None = None
        same_saved_selection = (
            row is not None
            and data.router_agent_id == row.router_agent_id
            and data.model_profile_id == row.model_profile_id
        )
        if data.enabled or not same_saved_selection:
            if data.router_agent_id is not None:
                version_id = await self._router_version(ctx, room, data.router_agent_id)
            if data.model_profile_id is not None:
                await self._validate_model_profile(ctx, data.model_profile_id)
        elif row is not None:
            # Turning the optional feature off must remain possible after its
            # pinned router or organization profile has been removed. Repeating
            # the saved pair preserves that inert configuration for a later
            # explicit re-enable; clearing both IDs discards it.
            version_id = row.router_version_id

        if row is None:
            row = RoomAutoRoutingPolicy(
                organization_id=ctx.organization_id,
                conversation_id=conversation_id,
                revision=1,
            )
        else:
            row.revision += 1
        row.enabled = data.enabled
        row.router_agent_id = data.router_agent_id
        row.router_version_id = version_id
        row.model_profile_id = data.model_profile_id
        row.quiet_window_ms = data.quiet_window_ms
        row.max_responders = data.max_responders
        await routing_repo.save_policy(self.db, row)
        return self._read(row, can_edit=True)
