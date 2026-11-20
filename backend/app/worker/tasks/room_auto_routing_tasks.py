"""Workers for metered, one-shot room responder selection."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.db.session import get_worker_db_context
from app.repositories import room_auto_routing as auto_repo
from app.repositories import room_submission as room_repo
from app.schemas.room_auto_routing import RoomAutoRoutingDecisionReceipt
from app.services.background_jobs import enqueue
from app.services.room_auto_routing import RoomAutoRoutingService
from app.worker.jobs import RUN_ROOM_AUTO_ROUTING, RoomAutoRoutingJob


async def run_auto_routing(job: RoomAutoRoutingJob) -> None:
    """Run one accepted router decision; a claimed provider call is never replayed."""
    async with get_worker_db_context() as db:
        await RoomAutoRoutingService(db).run_decision(job.organization_id, job.decision_id)


async def reconcile_auto_routing() -> int:
    """Queue due, unblocked decisions and close stale claims without replaying them."""
    async with get_worker_db_context() as db:
        now = datetime.now(UTC)
        due = await auto_repo.ready_decisions(db, organization_id=None, now=now)
        queued = 0
        for decision in due:
            root = await room_repo.get(
                db,
                decision.submission_id,
                organization_id=decision.organization_id,
            )
            if (
                root is None
                or await auto_repo.has_earlier_active_submission(db, decision)
                or await auto_repo.has_pending_or_running_before_submission(db, root)
            ):
                continue
            await enqueue(
                db,
                RUN_ROOM_AUTO_ROUTING,
                RoomAutoRoutingJob(
                    organization_id=decision.organization_id,
                    decision_id=decision.id,
                ),
                organization_id=decision.organization_id,
                run_after=decision.ready_at,
                dedupe_key=f"room-auto-routing:{decision.id}",
                dedupe_active=True,
                concurrency_key=f"room:{decision.conversation_id}",
            )
            queued += 1

        stale = await auto_repo.mark_stale_running_unknown(
            db,
            organization_id=None,
            before=now - timedelta(seconds=90),
            now=now,
        )
        return queued + stale


async def settle_lost_auto_routing(job: RoomAutoRoutingJob, _why: str) -> None:
    """Mark claimed decisions unknown; pending work is safe for the reconciler."""
    async with get_worker_db_context() as db:
        current = await auto_repo.get_decision(
            db,
            job.decision_id,
            organization_id=job.organization_id,
            lock=True,
        )
        if current is None or current.status != "running" or current.claim_token is None:
            return
        await auto_repo.finish_decision(
            db,
            current.id,
            organization_id=job.organization_id,
            claim_token=current.claim_token,
            claim_generation=current.claim_generation,
            receipt=RoomAutoRoutingDecisionReceipt(
                status="unknown",
                grouped_message_ids=[UUID(str(value)) for value in current.grouped_message_ids],
                router_run_id=current.router_run_id,
                error_code="worker_lost_after_claim",
            ),
            finished_at=datetime.now(UTC),
        )
