"""A run's own status read is not refused by its own comparison's claim.

The eval-selection e2e test polls `GET /eval-runs/{id}` while the run page's
comparison holds the per-org claim, and the poll 429s until the comparison
finishes. A read of the run the comparison is of is the comparison's own
reader, not a second comparison, so it keeps what the run has instead. A read
of any other run still refuses while the claim is held.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import RateLimitError
from app.core.permissions import AuthContext
from app.db.models.eval_run import EvalRun
from app.services import rate_limit
from app.services.eval_run import EvalRunService

pytestmark = [pytest.mark.anyio, pytest.mark.security]


def _ctx(org_id):
    return AuthContext(user_id=uuid4(), organization_id=org_id, role="owner")


def _run(run_id, org_id, *, suite_check=None):
    return EvalRun(
        id=run_id,
        organization_id=org_id,
        status="completed",
        outcome=None,
        outcome_at=None,
        suite_check=suite_check,
    )


@pytest.fixture(autouse=True)
def _local_claims(monkeypatch):
    """The claim is held per process, and the work budget always says yes."""
    monkeypatch.setattr(rate_limit, "_redis", None)
    monkeypatch.setattr(
        rate_limit,
        "consume_with_local_floor",
        AsyncMock(return_value=rate_limit.Decision(True, 60)),
    )


async def test_a_run_read_keeps_what_it_has_while_its_own_comparison_holds_the_claim(monkeypatch):
    """(a) The status poll is not refused by the run's own comparison."""
    org_id = uuid4()
    run_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    compared = AsyncMock()
    monkeypatch.setattr(service, "_compared_outcome", compared)
    async with rate_limit.eval_computation(ctx, run_id=run_id):
        assert await service._outcome(ctx, _run(run_id, org_id), admission="refuse") is None
    compared.assert_not_awaited()


async def test_a_run_read_still_refuses_while_another_run_comparison_holds_the_claim(monkeypatch):
    """(b) The refusal stands for any comparison that is not the run's own."""
    org_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    compared = AsyncMock()
    monkeypatch.setattr(service, "_compared_outcome", compared)
    async with rate_limit.eval_computation(ctx, run_id=uuid4()):
        with pytest.raises(RateLimitError):
            await service._outcome(ctx, _run(uuid4(), org_id), admission="refuse")
    compared.assert_not_awaited()


async def test_a_run_read_still_refuses_while_an_unnamed_claim_is_held(monkeypatch):
    org_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    compared = AsyncMock()
    monkeypatch.setattr(service, "_compared_outcome", compared)
    async with rate_limit.eval_computation(ctx):
        with pytest.raises(RateLimitError):
            await service._outcome(ctx, _run(uuid4(), org_id), admission="refuse")
    compared.assert_not_awaited()


async def test_a_run_read_compares_afresh_when_no_claim_is_held(monkeypatch):
    org_id = uuid4()
    run_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    fresh_outcome = object()
    compared = AsyncMock(return_value=fresh_outcome)
    monkeypatch.setattr(service, "_compared_outcome", compared)
    assert await service._outcome(ctx, _run(run_id, org_id), admission="refuse") is fresh_outcome
    compared.assert_awaited_once()


async def test_a_suite_verdict_read_keeps_what_it_has_while_its_own_comparison_holds_the_claim(
    monkeypatch,
):
    org_id = uuid4()
    run_id = uuid4()
    suite_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    monkeypatch.setattr("app.services.eval_run.eval_suite_repo.get", AsyncMock(return_value=None))
    judged = AsyncMock()
    monkeypatch.setattr(service, "_judge_suite", judged)
    run = _run(
        run_id,
        org_id,
        suite_check={"suite_id": str(suite_id), "name": "Suite", "threshold": {}, "judged": None},
    )
    async with rate_limit.eval_computation(ctx, run_id=run_id):
        verdict = await service._suite_check(ctx, run, MagicMock(), admission="refuse")
    assert verdict.suite_id == suite_id
    assert verdict.status is None
    judged.assert_not_awaited()


async def test_a_suite_verdict_read_still_refuses_while_another_run_comparison_holds_the_claim(
    monkeypatch,
):
    org_id = uuid4()
    suite_id = uuid4()
    ctx = _ctx(org_id)
    service = EvalRunService(MagicMock())
    monkeypatch.setattr("app.services.eval_run.eval_suite_repo.get", AsyncMock(return_value=None))
    judged = AsyncMock()
    monkeypatch.setattr(service, "_judge_suite", judged)
    run = _run(
        uuid4(),
        org_id,
        suite_check={"suite_id": str(suite_id), "name": "Suite", "threshold": {}, "judged": None},
    )
    async with rate_limit.eval_computation(ctx, run_id=uuid4()):
        with pytest.raises(RateLimitError):
            await service._suite_check(ctx, run, MagicMock(), admission="refuse")
    judged.assert_not_awaited()
