"""One person's favourite app."""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ArtifactFavourite(Base, TimestampMixin):
    """A preference on an app, scoped to the person who set it."""

    __tablename__ = "artifact_favourites"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<ArtifactFavourite(user_id={self.user_id}, artifact_id={self.artifact_id})>"
