from sqlalchemy import SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)

    events: Mapped[list["Event"]] = relationship("Event", back_populates="category")  # type: ignore[name-defined]  # noqa: F821

    def __repr__(self) -> str:
        return f"<Category {self.slug}>"
