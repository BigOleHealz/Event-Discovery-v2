import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("oauth_provider", "oauth_sub"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_provider: Mapped[str] = mapped_column(Text, nullable=False)
    oauth_sub: Mapped[str] = mapped_column(Text, nullable=False)
    # Encrypted Spotify token — stored as JSONB, never exposed in API responses directly
    spotify_token: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    invites_sent: Mapped[list["Invite"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Invite", back_populates="sender", foreign_keys="Invite.sender_id"
    )
    user_events: Mapped[list["UserEvent"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "UserEvent", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User {self.email!r}>"
