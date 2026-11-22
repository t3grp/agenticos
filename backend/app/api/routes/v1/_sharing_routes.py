"""One sharing API, generated per resource type.

Agents, collections and skills share the same sharing model - an owner, a
visibility, and a list of explicit grants to members or groups - because the access rules underneath
them are deliberately resource-type agnostic. Writing the same four endpoints
three times would guarantee they drift: the fix applied to one, the field added
to another.

So the routes are built once and stamped out per type. What varies is only which
table a row is loaded from, which is the single argument this factory takes.

There *are* three routers, not one: `sharing.py` builds and names them, and each
is mounted under its own prefix with its own OpenAPI tags, so a client reading
the schema sees `/agents/{id}/sharing` and `/skills/{id}/sharing` as the
separate endpoints they are. What is shared is the definition, not the router.
Writing them out three times would buy nothing but three places to fix the next
bug in.

Nothing here builds a response body. Turning rows into `ResourceSharing` is
`ResourceSharing.of` - a route module that also owns serialization is how the
fourth resource type ends up with a subtly different payload.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Auth, DBSession, SharingSvc
from app.core.permissions import AuthContext
from app.db.models.resource_grant import GrantLevel, Visibility
from app.schemas.resource_grant import (
    ResourceGrantBulkResult,
    ResourceGrantBulkUpsert,
    ResourceGrantRead,
    ResourceGrantUpsert,
    ResourceSharing,
    SharingDeferred,
    VisibilityUpdate,
)
from app.services.access import ResourceType
from app.services.sharing import Deferred, SharingService

_REQUESTED = (
    "For resource types whose guard defers widening: a request was filed instead of sharing, "
    "and nothing is applied until it is decided"
)


def _requested(deferred: Deferred) -> JSONResponse:
    body = SharingDeferred(request_id=deferred.request_id, waiting_for=deferred.waiting_for)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=jsonable_encoder(body))


# Loads one row of this resource type inside the caller's organization, or raises 404.
ResourceLoader = Callable[[AsyncSession, UUID, AuthContext], Awaitable[Any]]


def build_sharing_router(
    *,
    resource_type: ResourceType,
    load: ResourceLoader,
) -> APIRouter:
    """Six sharing endpoints for one resource type.

    The path parameter is `resource_id` for every type rather than `agent_id`,
    `kb_id` and so on: FastAPI binds path parameters by name, so per-type names
    would mean a separate handler per type - four near-identical functions for a
    cosmetic difference in the generated schema.

    Args:
        resource_type: Which permissions govern it, and the key stored on grants.
        load: How to fetch one row of it for the caller, inside their organization.
    """
    router = APIRouter()

    async def _resolve(db: AsyncSession, resource_id: UUID, ctx: AuthContext) -> Any:
        return await load(db, resource_id, ctx)

    async def _sharing_of(service: SharingService, ctx: Any, resource: Any) -> ResourceSharing:
        state = await service.get_sharing(ctx, resource, resource_type=resource_type)
        return ResourceSharing.of(
            resource,
            resource_type=resource_type.key,
            grants=state.grants,
            emails=state.emails,
            group_names=state.group_names,
        )

    async def _grant_one(
        service: SharingService, ctx: Any, resource: Any, data: ResourceGrantUpsert
    ) -> ResourceGrantRead | Deferred:
        """One grant, from one upsert item — the single and the bulk share agree.

        Omitted expiry and explicit null are different answers, and only the
        caller can tell them apart: `model_fields_set` is what distinguishes
        them. Omitted asks the service for its default — 90 days for a new
        grant, the expiry it already has for a level change; null asks for no
        expiry, which a group share refuses.
        """
        no_expiry = "expires_at" in data.model_fields_set and data.expires_at is None
        if data.subject_group_id is not None:
            grant = await service.share_with_group(
                ctx,
                resource,
                resource_type=resource_type,
                group_id=data.subject_group_id,
                level=GrantLevel(data.level),
                confirmed=data.confirmed,
                expires_at=data.expires_at,
                no_expiry=no_expiry,
                apply_default_expiry=True,
            )
        else:
            # `ResourceGrantUpsert` guarantees exactly one subject, so with no
            # group named there is a person.
            assert data.subject_user_id is not None
            grant = await service.share(
                ctx,
                resource,
                resource_type=resource_type,
                subject_user_id=data.subject_user_id,
                level=GrantLevel(data.level),
                confirmed=data.confirmed,
                expires_at=data.expires_at,
                clear_expiry=no_expiry,
                apply_default_expiry=True,
            )
        if isinstance(grant, Deferred):
            return grant
        return ResourceGrantRead.of(grant, emails={}, group_names={})

    @router.get("/{resource_id}/sharing", response_model=ResourceSharing)
    async def get_sharing(
        resource_id: UUID,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> Any:
        """Owner, visibility, and who this is shared with."""
        return await _sharing_of(service, ctx, await _resolve(db, resource_id, ctx))

    @router.put(
        "/{resource_id}/sharing/grants",
        response_model=ResourceGrantRead,
        responses={202: {"model": SharingDeferred, "description": _REQUESTED}},
    )
    async def share(
        resource_id: UUID,
        data: ResourceGrantUpsert,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> Any:
        """Share with a member or a group, or change the level they already have.

        Answers 202 with the request filed, and applies nothing, for a resource type whose
        guard defers widening to someone else (a page that reads live data, when the
        caller may edit it but is not the one who decides who sees its data).

        An omitted expiry is 90 days for a new grant and keeps the expiry a
        grant already has; an explicit null is a share with no expiry, which a
        group share refuses.
        """
        resource = await _resolve(db, resource_id, ctx)
        grant = await _grant_one(service, ctx, resource, data)
        if isinstance(grant, Deferred):
            return _requested(grant)
        return grant

    @router.put(
        "/{resource_id}/sharing/grants/bulk",
        response_model=ResourceGrantBulkResult,
    )
    async def share_bulk(
        resource_id: UUID,
        data: ResourceGrantBulkUpsert,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> Any:
        """Share with several members or groups in one call.

        Each item follows the single share — including its expiry rule. The
        whole call applies or nothing does: the first refusal aborts it. A
        grant the guard defers is not applied; it is listed in `requested`
        with who decides it, and the rest still land.
        """
        resource = await _resolve(db, resource_id, ctx)
        granted: list[ResourceGrantRead] = []
        requested: list[SharingDeferred] = []
        for item in data.grants:
            outcome = await _grant_one(service, ctx, resource, item)
            if isinstance(outcome, Deferred):
                requested.append(
                    SharingDeferred(request_id=outcome.request_id, waiting_for=outcome.waiting_for)
                )
            else:
                granted.append(outcome)
        return ResourceGrantBulkResult(granted=granted, requested=requested)

    @router.delete(
        "/{resource_id}/sharing/grants/{subject_user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
    )
    async def unshare(
        resource_id: UUID,
        subject_user_id: UUID,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> None:
        """Stop sharing with a member."""
        resource = await _resolve(db, resource_id, ctx)
        await service.revoke(
            ctx, resource, resource_type=resource_type, subject_user_id=subject_user_id
        )

    @router.delete(
        "/{resource_id}/sharing/group-grants/{group_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
    )
    async def unshare_group(
        resource_id: UUID,
        group_id: UUID,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> None:
        """Stop sharing with a group."""
        resource = await _resolve(db, resource_id, ctx)
        await service.revoke_group(ctx, resource, resource_type=resource_type, group_id=group_id)

    @router.patch(
        "/{resource_id}/sharing/visibility",
        response_model=ResourceSharing,
        responses={202: {"model": SharingDeferred, "description": _REQUESTED}},
    )
    async def set_visibility(
        resource_id: UUID,
        data: VisibilityUpdate,
        db: DBSession,
        service: SharingSvc,
        ctx: Auth,
    ) -> Any:
        """Make it private, team-visible or org-visible.

        Widening a resource whose guard defers it answers 202, as for a grant.
        """
        resource = await _resolve(db, resource_id, ctx)
        changed = await service.set_visibility(
            ctx, resource, resource_type=resource_type, visibility=Visibility(data.visibility)
        )
        if isinstance(changed, Deferred):
            return _requested(changed)
        return await _sharing_of(service, ctx, resource)

    return router
