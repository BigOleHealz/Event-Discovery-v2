import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    recipient_phone: Mapped[str] = mapped_column(Text, nullable=False)
    # 'pending' | 'delivered' | 'opened'
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    twilio_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    deep_link: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    event: Mapped["Event"] = relationship("Event", back_populates="invites")  # type: ignore[name-defined]  # noqa: F821
    sender: Mapped["User"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "User", back_populates="invites_sent", foreign_keys=[sender_id]
    )

    def __repr__(self) -> str:
        return f"<Invite {self.id} → {self.recipient_phone} ({self.status})>"
