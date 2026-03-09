import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, SmallInteger, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index("events_start_at_idx", "start_at"),
        Index("events_venue_id_idx", "venue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Self-referential: points to the canonical (dedup winner) event; NULL means this IS canonical
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(nullable=True)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("categories.id"), nullable=True
    )
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Qdrant point ID — deterministic UUID5 from {source}:{external_id}
    embedding_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    canonical: Mapped["Event | None"] = relationship(
        "Event", remote_side="Event.id", foreign_keys=[canonical_id], back_populates="duplicates"
    )
    duplicates: Mapped[list["Event"]] = relationship(
        "Event", foreign_keys=[canonical_id], back_populates="canonical"
    )
    venue: Mapped["Venue | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Venue", back_populates="events"
    )
    category: Mapped["Category | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Category", back_populates="events"
    )
    invites: Mapped[list["Invite"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Invite", back_populates="event"
    )
    user_events: Mapped[list["UserEvent"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "UserEvent", back_populates="event"
    )

    def __repr__(self) -> str:
        return f"<Event {self.title!r} @ {self.start_at}>"
