import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category


def _make_category(**overrides: object) -> Category:
    suffix = uuid.uuid4().hex[:6]
    defaults: dict[str, object] = {"slug": f"music-{suffix}", "label": "Music"}
    defaults.update(overrides)
    return Category(**defaults)  # type: ignore[arg-type]


async def test_create_category(db_session: AsyncSession) -> None:
    cat = _make_category(slug="tech", label="Technology")
    db_session.add(cat)
    await db_session.flush()

    assert cat.id is not None
    assert cat.slug == "tech"
    assert cat.label == "Technology"


async def test_category_repr(db_session: AsyncSession) -> None:
    cat = _make_category(slug="sports-repr")
    db_session.add(cat)
    await db_session.flush()
    assert "sports-repr" in repr(cat)


async def test_category_slug_unique(db_session: AsyncSession) -> None:
    slug = f"slug-{uuid.uuid4().hex[:6]}"
    db_session.add(_make_category(slug=slug, label="First"))
    await db_session.flush()

    db_session.add(_make_category(slug=slug, label="Second"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_category_autoincrement_id(db_session: AsyncSession) -> None:
    cat1 = _make_category()
    cat2 = _make_category()
    db_session.add_all([cat1, cat2])
    await db_session.flush()

    assert cat1.id != cat2.id
    assert isinstance(cat1.id, int)
