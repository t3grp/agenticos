"""Lease-bound teaching controls for managed browser runs."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import ConfigDict, Field

from app.api.deps import Auth, BrowserTeachingSvc, CurrentUser, require
from app.core.exceptions import BadRequestError
from app.core.permissions import Perm
from app.schemas.base import BaseSchema
from app.schemas.browser_teaching import (
    BrowserTeachingAction,
    BrowserTeachingRead,
    BrowserTeachingReview,
    BrowserTeachingStart,
)

router = APIRouter()


class BrowserTeachingPropose(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)


@router.post("", response_model=BrowserTeachingRead, status_code=status.HTTP_201_CREATED)
async def start_teaching(
    data: BrowserTeachingStart,
    request: Request,
    service: BrowserTeachingSvc,
    ctx: Auth,
    user: CurrentUser,
    _permission: None = Depends(require(Perm.SKILLS_EDIT)),
) -> Any:
    """Start capture only while the signed-in member owns this managed browser lease."""
    key = request.headers.get("Idempotency-Key", "")
    try:
        key = str(UUID(key))
    except ValueError:
        raise BadRequestError(
            message="Provide a valid Idempotency-Key to start teaching."
        ) from None
    return await service.start(ctx, user.id, data, key)


@router.get("/{teaching_id}", response_model=BrowserTeachingRead)
async def get_teaching(
    teaching_id: UUID, service: BrowserTeachingSvc, ctx: Auth, user: CurrentUser
) -> Any:
    """Read the caller's redacted sequence."""
    return await service.get(ctx, user.id, teaching_id)


@router.patch("/{teaching_id}", response_model=BrowserTeachingRead)
async def transition_teaching(
    teaching_id: UUID,
    data: BrowserTeachingAction,
    service: BrowserTeachingSvc,
    ctx: Auth,
    user: CurrentUser,
) -> Any:
    """Pause or resume capture with a current revision."""
    return await service.transition(ctx, user.id, teaching_id, data)


@router.post("/{teaching_id}/stop", response_model=BrowserTeachingRead)
async def stop_teaching(
    teaching_id: UUID, service: BrowserTeachingSvc, ctx: Auth, user: CurrentUser
) -> Any:
    """Close capture and open the review step."""
    return await service.stop(ctx, user.id, teaching_id)


@router.put("/{teaching_id}/review", response_model=BrowserTeachingRead)
async def review_teaching(
    teaching_id: UUID,
    data: BrowserTeachingReview,
    service: BrowserTeachingSvc,
    ctx: Auth,
    user: CurrentUser,
) -> Any:
    """Parameterize captured input steps without sending values back to the server."""
    return await service.review(ctx, user.id, teaching_id, data)


@router.post(
    "/{teaching_id}/propose",
    response_model=BrowserTeachingRead,
    dependencies=[Depends(require(Perm.SKILLS_EDIT))],
)
async def propose_teaching(
    teaching_id: UUID,
    data: BrowserTeachingPropose,
    service: BrowserTeachingSvc,
    ctx: Auth,
    user: CurrentUser,
) -> Any:
    """Create one deterministic, reviewable skill proposal after fresh permission checks."""
    return await service.propose(
        ctx, user.id, teaching_id, name=data.name, description=data.description
    )


@router.delete("/{teaching_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def discard_teaching(
    teaching_id: UUID, service: BrowserTeachingSvc, ctx: Auth, user: CurrentUser
) -> None:
    """Erase an unproposed demonstration and stop future capture."""
    await service.discard(ctx, user.id, teaching_id)
