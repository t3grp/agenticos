"""Unit tests for OnboardingTeamService."""

from unittest.mock import MagicMock

from app.schemas.onboarding import OnboardingProfileRequest
from app.services.onboarding_team import OnboardingTeamService


def test_propose_team_software() -> None:
    svc = OnboardingTeamService(db=MagicMock())
    req = OnboardingProfileRequest(
        role="Engineering Manager",
        industry="software",
        team_size="small",
        goals=["Releases", "API Support"],
    )
    proposal = svc.propose_team(req)
    assert proposal.industry == "software"
    assert "Tech Lead" in proposal.lead.name
    assert len(proposal.specialists) > 0
    assert any(s.key == "software/api-support" for s in proposal.specialists)


def test_propose_team_ecommerce() -> None:
    svc = OnboardingTeamService(db=MagicMock())
    req = OnboardingProfileRequest(
        role="Store Owner",
        industry="ecommerce",
        team_size="solo",
        goals=["Order tracking", "Listing copy"],
    )
    proposal = svc.propose_team(req)
    assert proposal.industry == "ecommerce"
    assert "Store" in proposal.lead.name
    assert len(proposal.specialists) > 0


def test_propose_team_general_fallback() -> None:
    svc = OnboardingTeamService(db=MagicMock())
    req = OnboardingProfileRequest(
        role="Operations",
        industry="unknown_industry_custom",
        team_size="company",
    )
    proposal = svc.propose_team(req)
    assert proposal.industry == "general"
    assert "Chief of Staff" in proposal.lead.name
    assert len(proposal.specialists) >= 2
