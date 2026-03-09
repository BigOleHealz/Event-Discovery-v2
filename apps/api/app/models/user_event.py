import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserEvent(Base):
    """Junction table tracking user actions on events (view, save, rsvp, invite_sent)."""

    __tablename__ = "user_events"
    __table_args__ = (Index("user_events_user_id_idx", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), primary_key=True
    )
    # 'view' | 'save' | 'rsvp' | 'invite_sent'
    action: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="user_events")  # type: ignore[name-defined]  # noqa: F821
    event: Mapped["Event"] = relationship("Event", back_populates="user_events")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserEvent user={self.user_id} event={self.event_id} action={self.action!r}>"
