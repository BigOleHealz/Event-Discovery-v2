import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


def _make_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "email": f"user_{uuid.uuid4().hex[:8]}@example.com",
        "oauth_provider": "google",
        "oauth_sub": uuid.uuid4().hex,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


async def test_create_user(db_session: AsyncSession) -> None:
    user = _make_user(name="Alice", avatar_url="https://example.com/avatar.png")
    db_session.add(user)
    await db_session.flush()

    assert user.id is not None
    assert user.email.endswith("@example.com")
    assert user.name == "Alice"
    assert user.created_at is not None


async def test_user_repr(db_session: AsyncSession) -> None:
    user = _make_user(email="repr@example.com")
    db_session.add(user)
    await db_session.flush()
    assert "repr@example.com" in repr(user)


async def test_user_email_unique(db_session: AsyncSession) -> None:
    email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
    db_session.add(_make_user(email=email))
    await db_session.flush()

    db_session.add(_make_user(email=email, oauth_sub=uuid.uuid4().hex))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_oauth_provider_sub_unique(db_session: AsyncSession) -> None:
    provider, sub = "google", f"sub_{uuid.uuid4().hex}"
    db_session.add(_make_user(oauth_provider=provider, oauth_sub=sub))
    await db_session.flush()

    db_session.add(_make_user(oauth_provider=provider, oauth_sub=sub))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_optional_fields_nullable(db_session: AsyncSession) -> None:
    user = _make_user()  # no name, no avatar_url, no spotify_token
    db_session.add(user)
    await db_session.flush()

    assert user.name is None
    assert user.avatar_url is None
    assert user.spotify_token is None


async def test_user_spotify_token_jsonb(db_session: AsyncSession) -> None:
    token_data = {"access_token": "tok_abc", "expires_in": 3600}
    user = _make_user(spotify_token=token_data)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    assert user.spotify_token == token_data
