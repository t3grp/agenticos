"""Seed a curated demo workspace for the guided first run.

Usage:
    agenticos cmd seed-demo <org-id>
    agenticos cmd seed-demo <org-id> --clear
    agenticos cmd seed-demo <org-id> --dry-run

What it creates, in the named organization:

- one project, "Demo — first-run example", visible to the whole organization,
  stored without a sandbox (``backend="state"``), so it needs no container;
- one conversation in it, "Demo — meeting notes into an action plan", with a
  user question and an assistant answer.

The content is the fictional Northwind example the guided first-run wizard
shows as "what a finished result looks like" (``frontend/messages/en.json``,
``start.examples.meeting``), so the wizard and the seed agree word for word.

What it deliberately does NOT create:

- no connected app: a connection row without credentials would be a fake, and
  the connections UI would try to refresh it and fail. The wizard's connect
  step is skippable for exactly this reason;
- no agent run: the assistant answer is stored with ``model_name="demo"`` and
  no ``run_id``, so it can never count as a real completed run. The wizard
  only completes on ``GET /me/onboarding`` reporting ``has_result``, which a
  seeded conversation cannot produce;
- no task board and no solution bundle: boards need a Virtual Table and the
  solutions backend is not on this branch yet (it arrives with the 1.86.1
  sync). Both are follow-ups, not silent omissions.

Idempotent: running it twice reports the existing workspace and creates
nothing. ``--clear`` removes exactly what it created — the demo conversation
first (deleting the project alone would only detach it, leaving an ordinary
chat behind), then the project — and nothing else.
"""

import asyncio
from uuid import UUID

import click
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands import command, error, info, success
from app.db.models.conversation import Conversation
from app.db.models.project import Project
from app.db.models.resource_grant import Visibility
from app.db.session import get_db_context
from app.repositories import conversation as conversation_repo
from app.repositories import member_repo, organization_repo
from app.repositories import project as project_repo

DEMO_PROJECT_NAME = "Demo — first-run example"
DEMO_PROJECT_DESCRIPTION = (
    "Demo workspace seeded by `agenticos cmd seed-demo`. Nothing here is real: "
    "the example conversation is fictional, no model ran, and no app is connected. "
    "Safe to delete."
)
DEMO_PROJECT_INSTRUCTIONS = (
    "This is the demo workspace for the guided first run. Treat its content as fictional examples."
)
DEMO_CONVERSATION_TITLE = "Demo — meeting notes into an action plan"
DEMO_USER_PROMPT = (
    "Turn these fictional meeting notes into an action plan with an owner and due date "
    "for each item. Flag missing information rather than inventing it.\n\n"
    "Northwind demo team: Maya will prepare the proposal by Friday. Leo will check "
    "the budget. The client needs a status update on Wednesday. We have not assigned "
    "the update or a budget deadline."
)
DEMO_ASSISTANT_REPLY = (
    "Maya — prepare proposal — Friday.\n"
    "Leo — check budget — deadline not specified.\n"
    "Client update — Wednesday — owner not assigned.\n"
    "Confirm the budget deadline and who will send the update."
)


async def _find_demo_project(db: AsyncSession, organization_id: UUID) -> Project | None:
    """The demo workspace, if this organization already has one."""
    result = await db.execute(
        select(Project).where(
            Project.organization_id == organization_id,
            Project.name == DEMO_PROJECT_NAME,
        )
    )
    return result.scalar_one_or_none()


async def _find_demo_conversation(
    db: AsyncSession, *, organization_id: UUID, project_id: UUID
) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(
            Conversation.organization_id == organization_id,
            Conversation.project_id == project_id,
            Conversation.title == DEMO_CONVERSATION_TITLE,
        )
    )
    return result.scalar_one_or_none()


@command("seed-demo", help="Seed a curated demo workspace for the guided first run")
@click.argument("organization_id")
@click.option("--clear", is_flag=True, help="Remove the demo workspace instead of creating it")
@click.option(
    "--dry-run", is_flag=True, help="Show what would be created without making changes"
)
def seed_demo(organization_id: str, clear: bool, dry_run: bool) -> None:
    asyncio.run(_run(organization_id, clear, dry_run))


async def _run(organization_id: str, clear: bool, dry_run: bool) -> None:
    try:
        org_id = UUID(organization_id)
    except ValueError:
        error(f"Not a UUID: {organization_id}")
        return

    async with get_db_context() as db:
        org = await organization_repo.get_by_id(db, org_id)
        if org is None:
            error(f"No organization with id {org_id}")
            return

        project = await _find_demo_project(db, org_id)

        if clear:
            if project is None:
                info("No demo workspace to remove.")
                return
            conversation = await _find_demo_conversation(
                db, organization_id=org_id, project_id=project.id
            )
            if conversation is not None:
                await db.delete(conversation)
                await db.flush()
            await project_repo.delete(db, project)
            success(f"Removed demo workspace from {org.name}")
            return

        if dry_run:
            info(f"[DRY RUN] Would create in {org.name}:")
            info(f"[DRY RUN]   project {DEMO_PROJECT_NAME!r}")
            info(f"[DRY RUN]   conversation {DEMO_CONVERSATION_TITLE!r} (2 messages)")
            if project is not None:
                info("[DRY RUN] Demo workspace already exists; nothing would be created.")
            return

        if project is not None:
            info(f"Demo workspace already exists in {org.name}; nothing created.")
            return

        owner_id = await member_repo.first_owner_id(db, organization_id=org_id)
        project = await project_repo.create(
            db,
            organization_id=org_id,
            owner_user_id=owner_id,
            name=DEMO_PROJECT_NAME,
            description=DEMO_PROJECT_DESCRIPTION,
            instructions=DEMO_PROJECT_INSTRUCTIONS,
            default_agent_id=None,
            backend="state",
            visibility=Visibility.ORG.value,
        )
        conversation = await conversation_repo.create_conversation(
            db,
            organization_id=org_id,
            user_id=owner_id,
            title=DEMO_CONVERSATION_TITLE,
            project_id=project.id,
        )
        await conversation_repo.create_message(
            db,
            conversation_id=conversation.id,
            role="user",
            content=DEMO_USER_PROMPT,
            author_user_id=owner_id,
        )
        await conversation_repo.create_message(
            db,
            conversation_id=conversation.id,
            role="assistant",
            content=DEMO_ASSISTANT_REPLY,
            model_name="demo",
        )
        success(
            f"Created demo workspace in {org.name}: "
            f"project {DEMO_PROJECT_NAME!r}, conversation {DEMO_CONVERSATION_TITLE!r}"
        )
