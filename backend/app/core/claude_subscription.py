"""Nonsecret identity passed to the isolated, native Claude runtime."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

CLAUDE_SUBSCRIPTION_PROVIDER = "claude-subscription"
type ClaudeSubscriptionModel = Literal["sonnet", "opus", "haiku"]
CLAUDE_SUBSCRIPTION_MODELS: tuple[ClaudeSubscriptionModel, ...] = ("sonnet", "opus", "haiku")


@dataclass(frozen=True)
class ClaudeSubscriptionOwner:
    """An already-authorized personal connection, never an OAuth credential."""

    organization_id: UUID
    user_id: UUID

    def payload(self) -> dict[str, str]:
        return {"organization_id": str(self.organization_id), "user_id": str(self.user_id)}
