"""Persist tool and router agent reactions with current room authority."""

import json
from collections.abc import Collection, Sequence
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.capabilities.room_reactions._contract import RoomReactionRunContext
from app.core.background import spawn_after_commit
from app.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.core.permissions import AuthContext
from app.db.locks import LockScope, hold_subject
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.room_agent_message_reaction import RoomAgentMessageReaction
from app.db.models.room_auto_routing import RoomAutoRoutingDecision
from app.db.session import get_db_context
from app.repositories import (
    room_agent_message_reaction as agent_reaction_repo,
)
from app.repositories import (
    room_auto_routing as auto_repo,
)
from app.repositories import (
    room_message_reaction as reaction_repo,
)
from app.repositories import (
    room_submission as room_repo,
)
from app.schemas.room_participant import RoomParticipantRef
from app.schemas.room_reaction import (
    MAX_DISTINCT_REACTIONS_PER_MESSAGE,
    RoomAgentMessageReactionRead,
    RoomMessageReactionCreate,
)
from app.services import chat_room
from app.services.access import acting_member
from app.services.agent_registry import AgentRegistryService
from app.services.room_auto_reaction import RoomAutoReaction
from app.services.room_participant import RoomParticipantService
from app.services.room_submission import RoomSubmissionService, cloud_agent
from app.services.room_thread_context_access import RoomThreadContextAccessService
from app.services.room_thread_context_read import RoomThreadContextReadService


def _same_action(
    row: RoomAgentMessageReaction,
    *,
    message_id: UUID,
    agent_id: UUID,
    version_id: UUID,
    emoji: str,
    reaction_only: bool,
) -> bool:
    return (
        row.message_id == message_id
        and row.agent_id == agent_id
        and row.agent_version_id == version_id
        and row.emoji == emoji
        and row.reaction_only == reaction_only
    )


def _in_thread(message: Message, thread_root_message_id: UUID | None) -> bool:
    if thread_root_message_id is None:
        return message.thread_root_message_id is None
    return (
        message.id == thread_root_message_id
        or message.thread_root_message_id == thread_root_message_id
    )


class RoomAgentReactionPort:
    """The hidden room action port for one trusted primary responder run."""

    def __init__(
        self,
        *,
        context: RoomReactionRunContext,
        run_id: UUID,
    ) -> None:
        self.context = context
        self.run_id = run_id
        self.reaction_only_tool_call_ids: set[str] = set()

    async def react(
        self,
        emoji: str,
        *,
        message_id: UUID | None,
        reaction_only: bool,
        tool_call_id: str,
    ) -> str:
        target_id = message_id or self.context.accepted_message_id
        async with get_db_context() as db:
            saved = await RoomAgentReactionService(db).record_tool(
                context=self.context,
                run_id=self.run_id,
                message_id=target_id,
                emoji=emoji,
                tool_call_id=tool_call_id,
                reaction_only=reaction_only,
            )
        if saved.reaction_only and saved.message_id == self.context.accepted_message_id:
            self.reaction_only_tool_call_ids.add(tool_call_id)
        return json.dumps(
            {
                "status": "reacted",
                "message_id": str(saved.message_id),
                "emoji": saved.emoji,
            },
            ensure_ascii=False,
        )


class RoomAgentReactionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_run(
        self,
        ctx: AuthContext,
        *,
        organization_id: UUID,
        run_id: UUID,
    ) -> tuple[list[RoomAgentMessageReactionRead], bool]:
        """Return a safe audit page after rechecking live room and source access."""
        if ctx.organization_id != organization_id:
            raise AuthorizationError(message="The run belongs to another organization")
        rows = await agent_reaction_repo.list_for_run(
            self.db,
            organization_id=organization_id,
            run_id=run_id,
        )
        visible_rows = rows[: agent_reaction_repo.MAX_RUN_REACTIONS_READ]
        rooms: dict[UUID, Conversation] = {}
        checked_contexts: set[tuple[UUID, UUID]] = set()
        # The lookahead row contributes to the public truncation flag, so it must
        # pass the same current room and inherited-context checks as visible rows.
        for row in rows:
            room = rooms.get(row.conversation_id)
            if room is None:
                room = await RoomSubmissionService(self.db).room(ctx, row.conversation_id)
                rooms[row.conversation_id] = room
            message = await reaction_repo.get_room_message(
                self.db,
                organization_id=organization_id,
                conversation_id=row.conversation_id,
                message_id=row.message_id,
            )
            if message is None:
                raise NotFoundError(message="A room reaction target is no longer available")
            root_message_id = message.thread_root_message_id or message.id
            context_key = (row.conversation_id, root_message_id)
            if context_key not in checked_contexts:
                await RoomThreadContextReadService(self.db).assert_history(
                    destination=room,
                    root_message_id=root_message_id,
                    actor=ctx,
                )
                checked_contexts.add(context_key)
        truncated = len(rows) > agent_reaction_repo.MAX_RUN_REACTIONS_READ
        return (
            [
                RoomAgentMessageReactionRead(
                    message_id=row.message_id,
                    agent_id=row.agent_id,
                    agent_name=row.agent_name,
                    agent_version_id=row.agent_version_id,
                    emoji=row.emoji,
                    reaction_only=row.reaction_only,
                    source=row.source,
                    tool_call_id=row.tool_call_id,
                    decision_id=row.decision_id,
                    created_at=row.created_at,
                )
                for row in visible_rows
            ],
            truncated,
        )

    @staticmethod
    def _emoji(value: str) -> str:
        try:
            return RoomMessageReactionCreate(emoji=value).emoji
        except ValidationError as error:
            raise BadRequestError(message="Reaction must be one emoji") from error

    async def _check_emoji_limit(
        self,
        *,
        organization_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        emoji: str,
    ) -> None:
        if await reaction_repo.has_emoji(
            self.db,
            organization_id=organization_id,
            conversation_id=conversation_id,
            message_id=message_id,
            emoji=emoji,
        ):
            return
        count = await reaction_repo.distinct_emoji_count(
            self.db,
            organization_id=organization_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if count >= MAX_DISTINCT_REACTIONS_PER_MESSAGE:
            raise BadRequestError(
                message="This message already has the maximum number of reactions",
                details={"maximum": MAX_DISTINCT_REACTIONS_PER_MESSAGE},
            )

    async def record_tool(
        self,
        *,
        context: RoomReactionRunContext,
        run_id: UUID,
        message_id: UUID,
        emoji: str,
        tool_call_id: str,
        reaction_only: bool,
    ) -> RoomAgentMessageReaction:
        if not tool_call_id or len(tool_call_id) > 128:
            raise BadRequestError(message="The room reaction tool call is invalid")
        await hold_subject(
            self.db,
            LockScope.CONVERSATION_TURN,
            context.conversation_id,
        )
        submission = await room_repo.get(
            self.db,
            context.submission_id,
            organization_id=context.organization_id,
            lock=True,
        )
        if (
            submission is None
            or submission.conversation_id != context.conversation_id
            or submission.requester_id != context.requester_id
            or submission.message_id != context.accepted_message_id
            or submission.thread_root_message_id != context.thread_root_message_id
            or submission.agent_id != context.agent_id
            or submission.version_id != context.agent_version_id
            or submission.status != "running"
            or submission.intent != "message"
            or submission.run_id != run_id
        ):
            raise AuthorizationError(message="This room responder no longer owns the action")

        ctx, conversation, accepted = await RoomSubmissionService(self.db).authority(submission)
        if (
            accepted.id != context.accepted_message_id
            or accepted.author_user_id != context.requester_id
            or accepted.role != "user"
        ):
            raise AuthorizationError(message="The accepted room message changed")
        run = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.organization_id == context.organization_id,
            )
        )
        if (
            run is None
            or run.status != "running"
            or run.surface != "web"
            or run.conversation_id != context.conversation_id
            or run.user_id != context.requester_id
            or run.agent_id != context.agent_id
            or run.agent_version_id != context.agent_version_id
            or run.parent_run_id is not None
        ):
            raise AuthorizationError(message="The room responder run is no longer active")

        message = await reaction_repo.get_room_message(
            self.db,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            message_id=message_id,
        )
        if message is None or not _in_thread(message, context.thread_root_message_id):
            raise NotFoundError(message="Room message not found in this thread")
        # The selected target can be a root message with its own imported
        # context. Recheck that target thread's source audience as well as the
        # submission thread that was authorized above.
        target_root_id = message.thread_root_message_id or message.id
        await RoomThreadContextReadService(self.db).assert_history(
            destination=conversation,
            root_message_id=target_root_id,
            actor=ctx,
            responder_agent_id=context.agent_id,
        )
        if message.agent_id == context.agent_id:
            raise AuthorizationError(message="An agent cannot react to its own message")
        if reaction_only and (
            message.id != context.accepted_message_id
            or message.role != "user"
            or message.author_user_id != context.requester_id
        ):
            raise BadRequestError(
                message="Reaction-only completion must react to the accepted human message"
            )
        normalized = self._emoji(emoji)
        existing = await agent_reaction_repo.by_tool_call(
            self.db,
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        if existing is not None:
            if _same_action(
                existing,
                message_id=message.id,
                agent_id=context.agent_id,
                version_id=context.agent_version_id,
                emoji=normalized,
                reaction_only=reaction_only,
            ):
                return existing
            raise ConflictError(message="This room reaction tool call already has another result")

        await self._check_emoji_limit(
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            message_id=message.id,
            emoji=normalized,
        )
        registry = AgentRegistryService(self.db)
        _, pinned_spec, _ = await registry.get_pinned_spec(
            ctx,
            context.agent_id,
            context.agent_version_id,
        )
        row = RoomAgentMessageReaction(
            id=uuid4(),
            organization_id=context.organization_id,
            conversation_id=context.conversation_id,
            message_id=message.id,
            agent_id=context.agent_id,
            agent_name=pinned_spec.name[:120],
            agent_version_id=context.agent_version_id,
            run_id=run_id,
            source="tool",
            submission_id=context.submission_id,
            decision_id=None,
            tool_call_id=tool_call_id,
            reaction_only=reaction_only,
            emoji=normalized,
        )
        await agent_reaction_repo.add(self.db, row)
        spawn_after_commit(
            self.db,
            chat_room.room_reaction(str(context.conversation_id), message.id),
            name="room-agent-message-reaction-changed",
        )
        return row

    async def record_automatic(
        self,
        *,
        organization_id: UUID,
        conversation_id: UUID,
        decision_id: UUID,
        claim_token: UUID,
        claim_generation: int,
        router_run_id: UUID,
        requester_id: UUID,
        thread_root_message_id: UUID | None,
        accepted_message_id: UUID,
        social_action: RoomAutoReaction,
        selected_agent_ids: Sequence[UUID],
    ) -> RoomAgentMessageReaction:
        """Persist one helper-selected social action before the router decision finishes."""
        if selected_agent_ids:
            raise BadRequestError(message="A selected responder suppresses automatic reactions")
        if social_action.message_id != accepted_message_id:
            raise BadRequestError(message="Automatic room reactions target the accepted human post")
        await hold_subject(self.db, LockScope.CONVERSATION_TURN, conversation_id)
        decision = await self.db.scalar(
            select(RoomAutoRoutingDecision)
            .where(
                RoomAutoRoutingDecision.id == decision_id,
                RoomAutoRoutingDecision.organization_id == organization_id,
                RoomAutoRoutingDecision.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if (
            decision is None
            or decision.status != "running"
            or decision.claim_token != claim_token
            or decision.claim_generation != claim_generation
            or decision.router_run_id != router_run_id
            or decision.requester_id != requester_id
            or decision.thread_root_message_id != thread_root_message_id
            or decision.accepted_message_id != accepted_message_id
        ):
            raise ConflictError(message="The automatic room reaction decision is no longer current")
        root = await room_repo.get(
            self.db,
            decision.submission_id,
            organization_id=organization_id,
            lock=True,
        )
        if (
            root is None
            or root.parent_submission_id is not None
            or root.status != "posted"
            or root.message_id != accepted_message_id
            or root.requester_id != requester_id
            or root.conversation_id != conversation_id
        ):
            raise ConflictError(message="The accepted automatic post changed")
        router_run = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.id == router_run_id,
                AgentRun.organization_id == organization_id,
            )
        )
        if (
            router_run is None
            or router_run.status != "completed"
            or router_run.surface != "web"
            or router_run.conversation_id is not None
            or router_run.agent_id != decision.router_agent_id
            or router_run.agent_version_id != decision.router_version_id
            or router_run.user_id != requester_id
            or router_run.parent_run_id is not None
        ):
            raise AuthorizationError(message="The automatic router run is not authoritative")

        snapshot = next(
            (
                item
                for item in decision.candidate_snapshot
                if str(item.get("agent_id")) == str(social_action.agent_id)
            ),
            None,
        )
        candidate_name = snapshot.get("name") if snapshot is not None else None
        if snapshot is None or not isinstance(candidate_name, str) or not candidate_name.strip():
            raise AuthorizationError(message="The reacting agent was not an accepted candidate")
        try:
            version_id = UUID(str(snapshot["version_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise AuthorizationError(
                message="The reacting agent was not an accepted candidate"
            ) from error
        name = candidate_name[:120]

        existing = await agent_reaction_repo.by_decision(
            self.db,
            organization_id=organization_id,
            conversation_id=conversation_id,
            decision_id=decision_id,
        )
        normalized = self._emoji(social_action.reaction)
        if existing is not None:
            if (
                existing.message_id == accepted_message_id
                and existing.agent_id == social_action.agent_id
                and existing.agent_version_id == version_id
                and existing.run_id == router_run_id
                and existing.emoji == normalized
            ):
                return existing
            raise ConflictError(message="This routing decision already has another social action")

        requester = await acting_member(self.db, organization_id, str(requester_id))
        if requester is None:
            raise AuthorizationError(message="The room requester no longer has access")
        rooms = RoomSubmissionService(self.db)
        room = await rooms.room(requester, conversation_id, for_write=True)
        await RoomThreadContextAccessService(self.db).assert_responder_visible(
            destination=room,
            responder_agent_id=social_action.agent_id,
        )
        if thread_root_message_id is not None:
            await RoomThreadContextReadService(self.db).assert_history(
                destination=room,
                root_message_id=thread_root_message_id,
                actor=requester,
                responder_agent_id=social_action.agent_id,
            )
        participants = await RoomParticipantService(self.db).validate_mentions(
            requester,
            conversation_id,
            [RoomParticipantRef(kind="agent", id=social_action.agent_id)],
        )
        if not participants or not participants[0].auto_reply:
            raise AuthorizationError(
                message="Automatic replies are no longer enabled for this agent"
            )

        current_policy = await auto_repo.policy(self.db, organization_id, conversation_id)
        if (
            current_policy is None
            or not current_policy.enabled
            or current_policy.revision != decision.policy_revision
            or current_policy.router_agent_id != decision.router_agent_id
            or current_policy.router_version_id != decision.router_version_id
        ):
            raise AuthorizationError(message="The automatic room routing policy changed")
        registry = AgentRegistryService(self.db)
        _, current_spec, _ = await registry.get_runnable_spec(requester, social_action.agent_id)
        _, pinned_spec, _ = await registry.get_pinned_spec(
            requester, social_action.agent_id, version_id
        )
        cloud_agent(current_spec)
        cloud_agent(pinned_spec)
        if pinned_spec.name[:120] != name:
            raise AuthorizationError(message="The accepted candidate identity changed")

        message = await reaction_repo.get_room_message(
            self.db,
            organization_id=organization_id,
            conversation_id=conversation_id,
            message_id=accepted_message_id,
        )
        if (
            message is None
            or message.role != "user"
            or message.author_user_id != requester_id
            or not _in_thread(message, thread_root_message_id)
        ):
            raise NotFoundError(message="The accepted human room message is unavailable")
        await RoomThreadContextReadService(self.db).assert_history(
            destination=room,
            root_message_id=message.thread_root_message_id or message.id,
            actor=requester,
            responder_agent_id=social_action.agent_id,
        )
        await self._check_emoji_limit(
            organization_id=organization_id,
            conversation_id=conversation_id,
            message_id=accepted_message_id,
            emoji=normalized,
        )
        row = RoomAgentMessageReaction(
            id=uuid4(),
            organization_id=organization_id,
            conversation_id=conversation_id,
            message_id=accepted_message_id,
            agent_id=social_action.agent_id,
            agent_name=name,
            agent_version_id=version_id,
            run_id=router_run_id,
            source="automatic",
            submission_id=None,
            decision_id=decision_id,
            tool_call_id=None,
            reaction_only=False,
            emoji=normalized,
        )
        await agent_reaction_repo.add(self.db, row)
        spawn_after_commit(
            self.db,
            chat_room.room_reaction(str(conversation_id), message.id),
            name="room-agent-message-reaction-changed",
        )
        return row


def should_suppress_empty_reaction_reply(
    *,
    run_status: str,
    output: str,
    called_tool_call_ids: Sequence[str],
    successful_reaction_only_tool_call_ids: Collection[str],
) -> bool:
    """Suppress only a clean, empty, solo reaction-only room completion."""
    return (
        run_status == "completed"
        and not output.strip()
        and len(called_tool_call_ids) == 1
        and called_tool_call_ids[0] in successful_reaction_only_tool_call_ids
    )
