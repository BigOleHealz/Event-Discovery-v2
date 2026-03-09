"""
Integration tests for PostGIS geospatial queries.

These tests verify that the GEOGRAPHY column, GIST index, and core
spatial functions (ST_DWithin, ST_Distance) behave correctly end-to-end.
"""

import uuid

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Venue

# Reference points
SF = (-122.4194, 37.7749)      # San Francisco (our "origin")
OAKLAND = (-122.2711, 37.8044)  # ~13 km from SF
LA = (-118.2437, 34.0522)       # ~560 km from SF


def _wkt(lng: float, lat: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def _geography_point(lng: float, lat: float):  # type: ignore[return]
    """SQLAlchemy expression for a literal geography point."""
    return func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)


async def _add_venue(db_session: AsyncSession, name: str, lng: float, lat: float) -> Venue:
    venue = Venue(name=name, location=_wkt(lng, lat))
    db_session.add(venue)
    await db_session.flush()
    return venue


# ── ST_DWithin ───────────────────────────────────────────────────────────────

async def test_st_dwithin_finds_nearby_venue(db_session: AsyncSession) -> None:
    """A venue in San Francisco should be found within a 50 km radius of SF."""
    await _add_venue(db_session, f"SF Venue {uuid.uuid4().hex[:4]}", *SF)

    origin = _geography_point(*SF)
    stmt = select(Venue).where(
        func.ST_DWithin(Venue.location, origin, 50_000)  # 50 km in metres
    )
    results = (await db_session.execute(stmt)).scalars().all()
    assert any(v.name.startswith("SF Venue") for v in results)


async def test_st_dwithin_excludes_distant_venue(db_session: AsyncSession) -> None:
    """A venue in LA should NOT appear in a 50 km radius search centred on SF."""
    la_name = f"LA Venue {uuid.uuid4().hex[:4]}"
    await _add_venue(db_session, la_name, *LA)

    origin = _geography_point(*SF)
    stmt = select(Venue).where(
        func.ST_DWithin(Venue.location, origin, 50_000)
    )
    results = (await db_session.execute(stmt)).scalars().all()
    assert not any(v.name == la_name for v in results)


async def test_st_dwithin_radius_boundary(db_session: AsyncSession) -> None:
    """Oakland (~13 km from SF) is inside 20 km but outside 5 km."""
    oakland_name = f"Oakland Venue {uuid.uuid4().hex[:4]}"
    await _add_venue(db_session, oakland_name, *OAKLAND)

    origin = _geography_point(*SF)

    inside = (await db_session.execute(
        select(Venue).where(func.ST_DWithin(Venue.location, origin, 20_000))
    )).scalars().all()
    assert any(v.name == oakland_name for v in inside)

    outside = (await db_session.execute(
        select(Venue).where(func.ST_DWithin(Venue.location, origin, 5_000))
    )).scalars().all()
    assert not any(v.name == oakland_name for v in outside)


async def test_st_dwithin_multiple_venues_ordering(db_session: AsyncSession) -> None:
    """Ordering by ST_Distance should rank closer venues first."""
    suffix = uuid.uuid4().hex[:4]
    sf_name = f"Near {suffix}"
    oak_name = f"Far {suffix}"

    await _add_venue(db_session, sf_name, *SF)
    await _add_venue(db_session, oak_name, *OAKLAND)

    origin = _geography_point(*SF)
    stmt = (
        select(Venue, func.ST_Distance(Venue.location, origin).label("dist_m"))
        .where(func.ST_DWithin(Venue.location, origin, 50_000))
        .where(Venue.name.in_([sf_name, oak_name]))
        .order_by("dist_m")
    )
    rows = (await db_session.execute(stmt)).all()
    names = [r[0].name for r in rows]

    assert names.index(sf_name) < names.index(oak_name)


# ── ST_Distance ───────────────────────────────────────────────────────────────

async def test_st_distance_same_point_is_zero(db_session: AsyncSession) -> None:
    venue = await _add_venue(db_session, f"Zero {uuid.uuid4().hex[:4]}", *SF)
    origin = _geography_point(*SF)

    dist = await db_session.scalar(
        select(func.ST_Distance(Venue.location, origin)).where(Venue.id == venue.id)
    )
    assert dist == pytest.approx(0.0, abs=1e-6)


async def test_st_distance_sf_to_oakland_approx(db_session: AsyncSession) -> None:
    """SF→Oakland distance should be roughly 12–14 km."""
    venue = await _add_venue(db_session, f"Oak {uuid.uuid4().hex[:4]}", *OAKLAND)
    origin = _geography_point(*SF)

    dist_m = await db_session.scalar(
        select(func.ST_Distance(Venue.location, origin)).where(Venue.id == venue.id)
    )
    assert dist_m is not None
    assert 12_000 < dist_m < 14_000


