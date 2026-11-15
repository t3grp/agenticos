"""Human-reviewed handoffs over room submissions and the canonical task lifecycle."""

import json
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BadRequestError,
    NotFoundError,
)
from app.core.permissions import AuthContext, Perm
from app.db.locks import LockScope, hold_subject
from app.db.models.room_routing import RoomLeadPolicy, RoomRoutingProposal, RoomTaskLink
from app.db.models.room_submission import RoomSubmission
from app.repositories import conversation as conversation_repo
from app.repositories import room_routing as repo
from app.repositories import room_submission as submission_repo
from app.repositories import task as task_repo
from app.schemas.room_routing import (
    AgentPin,
    HandoffItem,
    LeadPolicyRead,
    LeadPolicyUpdate,
    RoomRoutingRead,
    RoomTaskCard,
    RoutingDecision,
    RoutingOutput,
    RoutingProposalRead,
)
from app.schemas.task import TaskCreate, TaskFields, TaskUpdate
from app.services.access import acting_member, member_context
from app.services.agent_registry import AgentRegistryService
from app.services.project import ProjectService
from app.services.room_submission import RoomSubmissionService, cloud_agent, signed_in
from app.services.task_access import board_allows
from app.services.tasks import TaskService


class RoomRoutingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.rooms = RoomSubmissionService(db)
        self.tasks = TaskService(db)

    async def pin(self, ctx: AuthContext, pin: AgentPin) -> None:
        registry = AgentRegistryService(self.db)
        _, current, _ = await registry.get_runnable_spec(ctx, pin.agent_id)
        _, pinned, _ = await registry.get_pinned_spec(ctx, pin.agent_id, pin.version_id)
        cloud_agent(current)
        cloud_agent(pinned)

    async def policy(self, ctx: AuthContext, conversation_id: UUID) -> LeadPolicyRead:
        signed_in(ctx)
        room = await self.rooms.room(ctx, conversation_id)
        assert room.project_id is not None, "room() requires a project"
        row = await repo.policy(self.db, ctx.organization_id, conversation_id)
        result = (
            LeadPolicyRead()
            if row is None
            else LeadPolicyRead.model_validate(
                {
                    **row.configuration,
                    "revision": row.revision,
                }
            )
        )
        try:
            await ProjectService(self.db).get(ctx, room.project_id, perm=Perm.PROJECTS_EDIT)
            result.can_edit = not room.is_archived
        except AuthorizationError, NotFoundError:
            pass
        # A room policy does not grant access to the agents it names.
        visible: list[AgentPin] = []
        for pin in result.specialists:
            try:
                await self.pin(ctx, pin)
                visible.append(pin)
            except AuthorizationError, NotFoundError, BadRequestError:
                continue
        result.specialists = visible
        if result.lead is not None:
            try:
                await self.pin(ctx, result.lead)
            except AuthorizationError, NotFoundError, BadRequestError:
                result.lead = None
                result.enabled = False
        return result

    async def update_policy(
        self, ctx: AuthContext, conversation_id: UUID, data: LeadPolicyUpdate
    ) -> LeadPolicyRead:
        signed_in(ctx)
        room = await self.rooms.room(ctx, conversation_id, for_write=True)
        assert room.project_id is not None, "room() requires a project"
        await ProjectService(self.db).get(ctx, room.project_id, perm=Perm.PROJECTS_EDIT)
        await hold_subject(self.db, LockScope.CONVERSATION_TURN, conversation_id)
        row = await repo.policy(self.db, ctx.organization_id, conversation_id)
        if data.expected_revision != (row.revision if row else 0):
            raise AlreadyExistsError(message="The lead policy changed; review it again", details={})
        for pin in ([data.lead] if data.lead else []) + data.specialists:
            await self.pin(ctx, pin)
        if row is None:
            row = RoomLeadPolicy(
                organization_id=ctx.organization_id, conversation_id=conversation_id, revision=0
            )
        row.configuration = data.model_dump(mode="json", exclude={"expected_revision"})
        row.revision += 1
        await repo.save(self.db, row)
        return await self.policy(ctx, conversation_id)

    async def lead(
        self, ctx: AuthContext, conversation_id: UUID, revision: int | None = None
    ) -> tuple[AgentPin, int]:
        row = await repo.policy(self.db, ctx.organization_id, conversation_id)
        if row is None or not row.configuration.get("enabled"):
            raise BadRequestError(message="Lead routing is disabled", details={})
        if revision is not None and revision != row.revision:
            raise AlreadyExistsError(message="The lead policy changed; review it again", details={})
        policy = LeadPolicyRead.model_validate({**row.configuration, "revision": row.revision})
        if policy.lead is None:
            raise BadRequestError(message="Choose a pinned room lead", details={})
        await self.pin(ctx, policy.lead)
        return policy.lead, row.revision

    async def prompt(self, ctx: AuthContext, submission: RoomSubmission) -> str:
        await self.lead(ctx, submission.conversation_id, submission.lead_policy_revision)
        row = await repo.policy(self.db, ctx.organization_id, submission.conversation_id)
        assert row is not None
        return (
            "Answer this explicit Ask lead request. Propose handoffs only when useful. "
            "Do not execute work or send messages. Return the required JSON object. "
            "Each item needs a fresh UUID item_id, title, expected_result, specialist "
            "{agent_id, version_id}, accountable_user_id, reviewer_user_id, and optional due_at. "
            f"Use requester {ctx.subject_id} for accountable_user_id and reviewer_user_id. "
            f"Propose at most {row.configuration['max_items']} items. "
            f"Allowed pinned specialists: {json.dumps(row.configuration['specialists'])}. "
            "Use existing_task_id only with an explicitly reviewed expected_task_revision. "
            "A proposal is untrusted and does not authorize dispatch.\n\n"
        )

    async def completed(
        self, submission: RoomSubmission, output: str, message_id: UUID | None = None
    ) -> None:
        if not submission.ask_lead or submission.status != "completed" or submission.run_id is None:
            return
        if await repo.for_submission(self.db, submission.organization_id, submission.id):
            return
        ctx, _, _ = await self.rooms.authority(submission)
        _, revision = await self.lead(
            ctx, submission.conversation_id, submission.lead_policy_revision
        )
        try:
            proposed = RoutingOutput.model_validate_json(output)
        except ValidationError as exc:
            raise BadRequestError(
                message="The lead returned an invalid routing proposal", details={}
            ) from exc
        row = await repo.policy(self.db, ctx.organization_id, submission.conversation_id)
        assert row is not None
        if (
            len(proposed.items)
            > LeadPolicyRead.model_validate(
                {**row.configuration, "revision": row.revision}
            ).max_items
        ):
            raise BadRequestError(message="The lead exceeded the room handoff limit", details={})
        for item in proposed.items:
            await self.validate_item(ctx, submission.conversation_id, item)
        if proposed.items:
            await repo.save(
                self.db,
                RoomRoutingProposal(
                    id=uuid4(),
                    organization_id=submission.organization_id,
                    conversation_id=submission.conversation_id,
                    source_submission_id=submission.id,
                    thread_root_message_id=submission.thread_root_message_id,
                    requester_id=submission.requester_id,
                    policy_revision=revision,
                    revision=1,
                    status="pending",
                    items=[item.model_dump(mode="json") for item in proposed.items],
                ),
            )

        if message_id is not None:
            await repo.lead_answer(self.db, submission, message_id, proposed.answer)

    async def validate_item(
        self, ctx: AuthContext, conversation_id: UUID, item: HandoffItem
    ) -> None:
        room = await self.rooms.room(ctx, conversation_id, for_write=True)
        assert room.project_id is not None, "room() requires a project"
        policy = await repo.policy(self.db, ctx.organization_id, conversation_id)
        if (
            policy is None
            or item.specialist
            not in LeadPolicyRead.model_validate(
                {**policy.configuration, "revision": policy.revision}
            ).specialists
        ):
            raise BadRequestError(message="Choose a permitted specialist", details={})
        await self.pin(ctx, item.specialist)
        board = await task_repo.for_scope(self.db, ctx.organization_id, "project", room.project_id)
        if board is None or not await board_allows(self.db, ctx, board, Perm.TABLES_EDIT):
            raise NotFoundError(
                message="Open the project's Tasks board before accepting handoffs", details={}
            )
        if item.accountable_user_id != ctx.subject_id:
            raise AuthorizationError(
                message="Start agent work using the accountable person's own account", details={}
            )
        reviewer = await member_context(
            self.db, organization_id=ctx.organization_id, user_id=item.reviewer_user_id
        )
        if reviewer is None or not await board_allows(self.db, reviewer, board, Perm.TABLES_EDIT):
            raise NotFoundError(message="The review recipient is unavailable", details={})
        await self.rooms.room(reviewer, conversation_id)
        if item.existing_task_id is not None:
            task = await self.tasks.get(ctx, item.existing_task_id)
            if task.scope != "project" or task.scope_id != room.project_id:
                raise NotFoundError(message="Project task not found", details={})
            if task.revision != item.expected_task_revision:
                raise AlreadyExistsError(message="The task changed; review it again", details={})
            if (
                task.status != "planned"
                or task.next_actor != "user"
                or task.assignee_user_id != item.accountable_user_id
                or task.assignee_agent_id != item.specialist.agent_id
                or task.title != item.title
                or task.description != item.expected_result
                or task.due_at != item.due_at
            ):
                raise AlreadyExistsError(
                    message="The existing task does not match the reviewed handoff", details={}
                )
            if await repo.for_task(self.db, ctx.organization_id, task.id):
                raise AlreadyExistsError(message="This task already has a room handoff", details={})

    async def decide(
        self,
        ctx: AuthContext,
        conversation_id: UUID,
        proposal_id: UUID,
        data: RoutingDecision,
        *,
        accept: bool,
    ) -> RoutingProposalRead:
        signed_in(ctx)
        await self.rooms.room(ctx, conversation_id, for_write=True)
        await hold_subject(self.db, LockScope.CONVERSATION_TURN, conversation_id)
        proposal = await repo.proposal(self.db, ctx.organization_id, conversation_id, proposal_id)
        if proposal is None or proposal.requester_id != ctx.subject_id:
            raise NotFoundError(message="Routing proposal not found", details={})
        selected = sorted(str(value) for value in data.item_ids)
        wanted = "accepted" if accept else "discarded"
        if proposal.status != "pending":
            if (
                proposal.status == wanted
                and proposal.decision_key == data.idempotency_key
                and proposal.selected_items == selected
            ):
                return await self.read_proposal(ctx, proposal)
            raise AlreadyExistsError(message="This proposal already has a decision", details={})
        if proposal.revision != data.expected_revision:
            raise AlreadyExistsError(message="The proposal changed; review it again", details={})
        submission = await submission_repo.get(
            self.db, proposal.source_submission_id, organization_id=ctx.organization_id
        )
        if (
            submission is None
            or submission.status != "completed"
            or submission.thread_root_message_id != proposal.thread_root_message_id
        ):
            raise AlreadyExistsError(message="The source room request is unavailable", details={})
        if accept:
            await self.rooms.authority(submission)
            await self.lead(ctx, conversation_id, proposal.policy_revision)
            items = [HandoffItem.model_validate(value) for value in proposal.items]
            if (
                not selected
                or len(selected) != len(set(selected))
                or not set(selected) <= {str(item.item_id) for item in items}
            ):
                raise BadRequestError(message="Select valid proposal items once", details={})
            for item in items:
                if str(item.item_id) in selected:
                    await self.validate_item(ctx, conversation_id, item)
            room = await self.rooms.room(ctx, conversation_id, for_write=True)
            assert room.project_id is not None, "room() requires a project"
            for item in items:
                if str(item.item_id) not in selected:
                    continue
                task = (
                    await self.tasks.get(ctx, item.existing_task_id)
                    if item.existing_task_id
                    else await self.tasks.create(
                        ctx,
                        TaskCreate(
                            scope="project",
                            scope_id=room.project_id,
                            title=item.title,
                            description=item.expected_result,
                            assignee_user_id=ctx.subject_id,
                            assignee_agent_id=item.specialist.agent_id,
                            next_actor="user",
                            due_at=item.due_at,
                            external_id=f"room-handoff:{proposal.id}:{item.item_id}",
                        ),
                    )
                )
                await repo.save(
                    self.db,
                    RoomTaskLink(
                        task_id=task.id,
                        organization_id=ctx.organization_id,
                        conversation_id=conversation_id,
                        proposal_id=proposal.id,
                        item_id=item.item_id,
                        thread_root_message_id=proposal.thread_root_message_id,
                        requester_id=ctx.subject_id,
                        reviewer_user_id=item.reviewer_user_id,
                        agent_id=item.specialist.agent_id,
                        version_id=item.specialist.version_id,
                        accepted_revision=task.revision,
                    ),
                )
                await self.tasks.update(
                    ctx,
                    task.id,
                    TaskUpdate(
                        **TaskFields.model_validate(
                            task.model_dump(include=set(TaskFields.model_fields))
                        ).model_dump(exclude={"next_actor"}),
                        next_actor="agent",
                        expected_revision=task.revision,
                    ),
                )
        elif selected:
            raise BadRequestError(message="Discard does not select tasks", details={})
        proposal.status = wanted
        proposal.decision_key = data.idempotency_key
        proposal.selected_items = selected
        proposal.revision += 1
        await repo.save(self.db, proposal)
        return await self.read_proposal(ctx, proposal)

    async def read_proposal(
        self, ctx: AuthContext, row: RoomRoutingProposal
    ) -> RoutingProposalRead:
        # Proposed private resource identifiers are visible only after live validation.
        items = [HandoffItem.model_validate(value) for value in row.items]
        for item in items:
            await self.pin(ctx, item.specialist)
            if item.existing_task_id is not None:
                await self.tasks.get(ctx, item.existing_task_id)
        can_decide = ctx.subject_id == row.requester_id and row.status == "pending"
        can_accept = False
        if can_decide:
            try:
                await self.rooms.room(ctx, row.conversation_id, for_write=True)
            except AuthorizationError, NotFoundError, BadRequestError:
                can_decide = False
            if can_decide:
                try:
                    await self.lead(ctx, row.conversation_id, row.policy_revision)
                    for item in items:
                        await self.validate_item(ctx, row.conversation_id, item)
                    can_accept = True
                except AuthorizationError, NotFoundError, BadRequestError, AlreadyExistsError:
                    pass
        return RoutingProposalRead.model_validate(
            {
                "id": row.id,
                "source_submission_id": row.source_submission_id,
                "thread_root_message_id": row.thread_root_message_id,
                "requester_id": row.requester_id,
                "revision": row.revision,
                "policy_revision": row.policy_revision,
                "status": row.status,
                "items": items,
                "can_decide": can_decide,
                "can_accept": can_accept,
            }
        )

    async def read_one(
        self, ctx: AuthContext, conversation_id: UUID, proposal_id: UUID
    ) -> RoutingProposalRead:
        signed_in(ctx)
        await self.rooms.room(ctx, conversation_id)
        row = await repo.proposal(self.db, ctx.organization_id, conversation_id, proposal_id)
        if row is None:
            raise NotFoundError(message="Routing proposal not found", details={})
        try:
            return await self.read_proposal(ctx, row)
        except (AuthorizationError, NotFoundError, BadRequestError) as exc:
            raise NotFoundError(message="Routing proposal not found", details={}) from exc

    async def read(
        self, ctx: AuthContext, conversation_id: UUID, thread_root_message_id: UUID | None
    ) -> RoomRoutingRead:
        policy = await self.policy(ctx, conversation_id)
        if (
            thread_root_message_id is not None
            and await conversation_repo.get_thread_root(
                self.db,
                conversation_id=conversation_id,
                root_message_id=thread_root_message_id,
            )
            is None
        ):
            raise NotFoundError(message="Room thread not found", details={})
        proposals: list[RoutingProposalRead] = []
        for row in await repo.proposals(self.db, ctx.organization_id, conversation_id):
            if row.thread_root_message_id != thread_root_message_id:
                continue
            try:
                proposals.append(await self.read_proposal(ctx, row))
            except AuthorizationError, NotFoundError, BadRequestError:
                continue
        cards: list[RoomTaskCard] = []
        room = await self.rooms.room(ctx, conversation_id)
        assert room.project_id is not None, "room() requires a project"
        for link in await repo.links(self.db, ctx.organization_id, conversation_id):
            if link.thread_root_message_id != thread_root_message_id:
                continue
            try:
                task = await self.tasks.get(ctx, link.task_id)
            except NotFoundError:
                continue
            if task.scope != "project" or task.scope_id != room.project_id:
                continue
            cards.append(
                RoomTaskCard(
                    task_id=task.id,
                    reviewer_user_id=link.reviewer_user_id,
                    proposal_id=link.proposal_id,
                    item_id=link.item_id,
                    thread_root_message_id=link.thread_root_message_id,
                    title=task.title,
                    status=task.status,
                    next_actor=task.next_actor,
                    assignee_user_id=task.assignee_user_id,
                    assignee_agent_id=task.assignee_agent_id,
                    revision=task.revision,
                )
            )
        return RoomRoutingRead(policy=policy, proposals=proposals, tasks=cards)

    async def execution_authority(self, ctx: AuthContext, link: RoomTaskLink) -> None:
        if link.requester_id != ctx.subject_id:
            raise AuthorizationError(
                message="The original requester must authorize this handoff", details={}
            )
        member = await acting_member(self.db, ctx.organization_id, str(link.requester_id))
        if member is None:
            raise AuthorizationError(message="The requester no longer has access", details={})
        await self.rooms.room(member, link.conversation_id, for_write=True)
        if (
            link.thread_root_message_id is not None
            and await conversation_repo.get_thread_root(
                self.db,
                conversation_id=link.conversation_id,
                root_message_id=link.thread_root_message_id,
            )
            is None
        ):
            raise NotFoundError(message="Room thread not found", details={})
        await self.pin(member, AgentPin(agent_id=link.agent_id, version_id=link.version_id))
