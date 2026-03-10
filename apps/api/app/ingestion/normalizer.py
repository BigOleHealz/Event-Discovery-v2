"""
Source-agnostic event normaliser.

Each source has a dedicated `normalize_<source>` function that maps that
source's raw payload to the shared `UnifiedEvent` schema.  The top-level
`normalize` dispatcher routes by source name.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from dateutil import parser as _dtp
from pydantic import BaseModel, ConfigDict

from app.ingestion.base import RawEvent


class UnifiedEvent(BaseModel):
    """
    Canonical intermediate representation of a single event.

    Created by `normalize()`, consumed by `dedup_service` and
    `event_service.upsert()`.  Matches the schema documented in CLAUDE.md §2.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str
    external_id: str
    title: str
    description: str | None
    start_at: datetime
    end_at: datetime | None
    lat: float
    lng: float
    venue_name: str | None
    venue_address: str | None = None
    city: str | None = None
    # Google Places ID — populated for SerpApi events that carry a venue.link
    # with a ludocid.  Used as the primary dedup key for venues.
    venue_place_id: str | None = None
    venue_phone: str | None = None
    venue_website: str | None = None
    category_slug: str | None
    ticket_url: str | None
    # All ticket/info links with their source name and link type.
    # Shape: [{"source": "Ticketmaster", "url": "https://...", "type": "tickets"}, ...]
    ticket_links: list[dict[str, str]] | None = None
    price_min: Decimal | None
    price_max: Decimal | None
    image_url: str | None
    raw_payload: dict

    # Set by dedup_service after similarity check
    canonical_id: str | None = None


# ---------------------------------------------------------------------------
# Embed-text builder
# ---------------------------------------------------------------------------

def build_embed_text(event: UnifiedEvent) -> str:
    """
    Construct the string that gets embedded by OpenAI.

    Template (from CLAUDE.md §1.3):
        {title} | {category} | {venue_name} | {city} | {date_YYYY-MM-DD} | {description[:500]}
    """
    date_str = event.start_at.strftime("%Y-%m-%d")
    desc = (event.description or "")[:500]
    parts = [
        event.title,
        event.category_slug or "",
        event.venue_name or "",
        event.city or "",
        date_str,
        desc,
    ]
    return " | ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Per-source normalisers
# ---------------------------------------------------------------------------

def _to_decimal(value: str | int | float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Eventbrite and Meetup both use ISO-8601; fromisoformat handles Z suffix in
    # Python 3.11+ but we normalise the Z manually for 3.12 compat.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- Eventbrite -----------------------------------------------------------

# Rough mapping from Eventbrite category IDs/names to our slugs
_EVENTBRITE_CATEGORY_MAP: dict[str, str] = {
    "Music": "music",
    "Business & Professional": "business",
    "Food & Drink": "food",
    "Community & Culture": "community",
    "Performing & Visual Arts": "arts",
    "Film, Media & Entertainment": "entertainment",
    "Sports & Fitness": "sports",
    "Health & Wellness": "wellness",
    "Science & Technology": "tech",
    "Travel & Outdoor": "outdoor",
    "Charity & Causes": "charity",
    "Religion & Spirituality": "spirituality",
    "Family & Education": "family",
    "Government & Politics": "politics",
    "Fashion & Beauty": "fashion",
    "Home & Lifestyle": "lifestyle",
    "Auto, Boat & Air": "auto",
    "Hobbies & Special Interest": "hobbies",
    "Other": "other",
    "School Activities": "education",
}


def normalize_eventbrite(raw: RawEvent) -> UnifiedEvent:
    d = raw.raw_data
    venue = d.get("venue") or {}
    addr = venue.get("address") or {}
    category = d.get("category") or {}

    lat_raw = venue.get("latitude") or venue.get("lat")
    lng_raw = venue.get("longitude") or venue.get("lng")

    price_min = _to_decimal(
        (d.get("ticket_availability") or {}).get("minimum_ticket_price", {}).get("major_value")
    )
    price_max = _to_decimal(
        (d.get("ticket_availability") or {}).get("maximum_ticket_price", {}).get("major_value")
    )

    category_name = category.get("short_name") or category.get("name") or ""
    category_slug = _EVENTBRITE_CATEGORY_MAP.get(category_name)

    logo = d.get("logo") or {}

    # The org-scoped endpoint returns name/description as plain strings.
    # The old search endpoint returned {"text": "...", "html": "..."} objects.
    # Handle both shapes so tests and legacy payloads still work.
    name_field = d.get("name") or ""
    title = name_field.get("text", "") if isinstance(name_field, dict) else name_field

    desc_field = d.get("description")
    description = (
        desc_field.get("text") if isinstance(desc_field, dict) else desc_field
    ) if desc_field else None

    return UnifiedEvent(
        source="eventbrite",
        external_id=str(d["id"]),
        title=title,
        description=description,
        start_at=_parse_dt(d.get("start", {}).get("utc")) or datetime.utcnow(),
        end_at=_parse_dt(d.get("end", {}).get("utc")),
        lat=float(lat_raw) if lat_raw else 0.0,
        lng=float(lng_raw) if lng_raw else 0.0,
        venue_name=venue.get("name"),
        venue_address=addr.get("localized_address_display"),
        city=addr.get("city"),
        category_slug=category_slug,
        ticket_url=d.get("url"),
        price_min=price_min,
        price_max=price_max,
        image_url=logo.get("url") or logo.get("original", {}).get("url"),
        raw_payload=d,
    )


# --- Meetup ---------------------------------------------------------------

_MEETUP_CATEGORY_MAP: dict[str, str] = {
    "tech": "tech",
    "science": "tech",
    "outdoors-adventure": "outdoor",
    "sports-fitness": "sports",
    "arts-culture": "arts",
    "social": "community",
    "education-learning": "education",
    "food-drink": "food",
    "music": "music",
    "lgbtq": "community",
    "film-media": "entertainment",
    "career-business": "business",
    "health-wellness": "wellness",
    "family": "family",
    "games-comic-cons": "hobbies",
    "fashion-beauty": "fashion",
    "religion-spirituality": "spirituality",
    "language-culture": "culture",
    "writing": "arts",
    "hobby-craft": "hobbies",
    "pets-animals": "hobbies",
    "support": "community",
    "book-clubs": "community",
    "movements-politics": "politics",
}


def normalize_meetup(raw: RawEvent) -> UnifiedEvent:
    d = raw.raw_data
    venue = d.get("venue") or {}
    group = d.get("group") or {}

    category_key = (group.get("category") or {}).get("urlkey") or ""
    category_slug = _MEETUP_CATEGORY_MAP.get(category_key)

    return UnifiedEvent(
        source="meetup",
        external_id=str(d["id"]),
        title=d.get("name") or d.get("title") or "",
        description=d.get("description"),
        start_at=_parse_dt(d.get("time") or d.get("dateTime")) or datetime.utcnow(),
        end_at=_parse_dt(d.get("endTime")),
        lat=float(venue.get("lat") or 0.0),
        lng=float(venue.get("lon") or venue.get("lng") or 0.0),
        venue_name=venue.get("name"),
        venue_address=venue.get("address_1"),
        city=venue.get("city"),
        category_slug=category_slug,
        ticket_url=d.get("link") or d.get("eventUrl"),
        price_min=_to_decimal((d.get("feeSettings") or d.get("fee") or {}).get("amount")),
        price_max=_to_decimal((d.get("feeSettings") or d.get("fee") or {}).get("amount")),
        image_url=(d.get("featured_photo") or {}).get("photo_link"),
        raw_payload=d,
    )


# --- SerpApi (Google Events) -----------------------------------------------

# Keyword → category slug map for title/description inference.
# Checked in order; first match wins.
_SERPAPI_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("concert", "music"),
    ("festival", "music"),
    ("music", "music"),
    ("comedy", "entertainment"),
    ("stand-up", "entertainment"),
    ("movie", "entertainment"),
    ("film", "entertainment"),
    ("ballet", "arts"),
    ("theater", "arts"),
    ("theatre", "arts"),
    ("art", "arts"),
    ("dance", "arts"),
    ("yoga", "wellness"),
    ("wellness", "wellness"),
    ("fitness", "sports"),
    ("sport", "sports"),
    ("run", "sports"),
    ("marathon", "sports"),
    ("health", "wellness"),
    ("food", "food"),
    ("drink", "food"),
    ("tasting", "food"),
    ("brunch", "food"),
    ("tech", "tech"),
    ("technology", "tech"),
    ("science", "tech"),
    ("hackathon", "tech"),
    ("startup", "tech"),
    ("family", "family"),
    ("kids", "family"),
    ("children", "family"),
    ("business", "business"),
    ("networking", "business"),
    ("career", "business"),
    ("outdoor", "outdoor"),
    ("hike", "outdoor"),
    ("nature", "outdoor"),
    ("charity", "charity"),
    ("fundraiser", "charity"),
    ("workshop", "education"),
    ("class", "education"),
    ("lecture", "education"),
    ("education", "education"),
    ("community", "community"),
    ("social", "community"),
    ("meetup", "community"),
]


def _infer_category(title: str, description: str | None) -> str | None:
    """Infer a category slug from whole-word keywords in the title and description."""
    text = f"{title} {description or ''}".lower()
    for keyword, slug in _SERPAPI_CATEGORY_KEYWORDS:
        if re.search(r"\b" + re.escape(keyword) + r"\b", text):
            return slug
    return None


# Timezone abbreviation → UTC offset hours (used to strip the abbr cleanly)
_TZ_ABBRS_RE = re.compile(
    r"\b(?:EST|EDT|CST|CDT|MST|MDT|PST|PDT|AKST|AKDT|HST|GMT|UTC)\s*$"
)
_DOW_RE = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s*", re.IGNORECASE)
_MONTH_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", re.IGNORECASE
)


def _parse_serpapi_when(
    when: str | None,
) -> tuple[datetime | None, datetime | None]:
    """
    Parse SerpApi's ``date.when`` string into (start_dt, end_dt) UTC datetimes.

    Handled formats:
      "Dec 2, 9:00 PM – Dec 30, 10:30 PM CST"
      "Sun, Dec 7, 8:00 – 9:30 PM CST"
      "Fri, Oct 7, 7 – 8 AM"
      "Nov 22, 4 PM – Dec 20, 8 PM CST"
    """
    if not when:
        return None, None

    # Split on em dash (–) or en dash (—)
    parts = re.split(r"\s*[–—]\s*", when, maxsplit=1)
    start_raw = parts[0].strip()
    end_raw = parts[1].strip() if len(parts) > 1 else ""

    # Strip timezone abbreviation from both sides
    start_raw = _TZ_ABBRS_RE.sub("", start_raw).strip()
    end_raw = _TZ_ABBRS_RE.sub("", end_raw).strip()

    # Strip "Sun, " style day-of-week prefix from start only
    start_raw = _DOW_RE.sub("", start_raw).strip()

    now = datetime.now()

    def _parse(s: str, default: datetime) -> datetime | None:
        if not s:
            return None
        try:
            return _dtp.parse(s, default=default, fuzzy=True)
        except (ValueError, OverflowError, TypeError):
            return None

    # Parse start with current year; promote to next year if already past
    default_this_year = datetime(now.year, now.month, now.day)
    start_dt = _parse(start_raw, default_this_year)
    if start_dt is None:
        return None, None
    if start_dt.date() < now.date() and str(now.year) not in start_raw:
        next_year_dt = _parse(start_raw, datetime(now.year + 1, now.month, now.day))
        if next_year_dt and next_year_dt.date() >= now.date():
            start_dt = next_year_dt

    # Localise to UTC (we drop the tz abbreviation; times are stored as-is)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    end_dt = None
    if end_raw:
        if _MONTH_RE.search(end_raw):
            # End string contains a month → full date
            end_dt = _parse(end_raw, datetime(start_dt.year, start_dt.month, start_dt.day))
        else:
            # Time-only end → same calendar day as start
            ref = start_dt.replace(tzinfo=None)
            end_dt = _parse(end_raw, ref)

        if end_dt is not None and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

    return start_dt, end_dt


def normalize_serpapi(raw: RawEvent) -> UnifiedEvent:
    d = raw.raw_data

    # Address array: [street+venue, "City, State"] or just ["City, State"]
    address_parts: list[str] = d.get("address") or []
    venue_address = ", ".join(address_parts) if address_parts else None
    city = address_parts[-1].strip() if address_parts else None

    # Venue name: prefer the nested `venue` object, fall back to first address part
    venue_obj = d.get("venue") or {}
    if venue_obj.get("name"):
        venue_name: str | None = venue_obj["name"]
    elif address_parts:
        # First element is typically "Venue Name, Street" — grab the venue portion
        venue_name = address_parts[0].split(",")[0].strip() or None
    else:
        venue_name = None

    # Dates
    date_info = d.get("date") or {}
    start_dt, end_dt = _parse_serpapi_when(date_info.get("when"))
    if start_dt is None:
        start_dt = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # Collect all ticket/info links preserving source and type metadata
    ticket_links: list[dict[str, str]] | None = None
    raw_ticket_info: list[dict] = d.get("ticket_info") or []
    if raw_ticket_info:
        ticket_links = [
            {
                "source": ti.get("source", ""),
                "url": ti.get("link", ""),
                "type": ti.get("link_type", ""),
            }
            for ti in raw_ticket_info
            if ti.get("link")
        ] or None

    # Primary ticket_url: first "tickets" type link, fall back to first any link,
    # then the event's own canonical URL.
    ticket_url: str | None = None
    for ti in raw_ticket_info:
        if ti.get("link_type") == "tickets" and ti.get("link"):
            ticket_url = ti["link"]
            break
    if not ticket_url and raw_ticket_info:
        ticket_url = raw_ticket_info[0].get("link")
    if not ticket_url:
        ticket_url = d.get("link")

    # Image: `image` is higher resolution than `thumbnail`
    image_url: str | None = d.get("image") or d.get("thumbnail")

    # Venue enrichment via Google Places API (requires GOOGLE_PLACES_API_KEY).
    # Deferred imports keep module load fast; both are lightweight singletons.
    from app.config import settings  # noqa: PLC0415
    from app.services.places_service import lookup_venue  # noqa: PLC0415

    venue_link: str | None = (d.get("venue") or {}).get("link")
    maps_link: str | None = (d.get("event_location_map") or {}).get("link")
    place_info = lookup_venue(
        venue_link=venue_link,
        venue_name=venue_name,
        city=city,
        api_key=settings.google_places_api_key,
        maps_link=maps_link,
    )

    if place_info:
        lat, lng = place_info.lat, place_info.lng
        # Prefer Places-supplied name/address as they are canonically formatted
        venue_name = place_info.name or venue_name
        venue_address = place_info.formatted_address or venue_address
        venue_place_id: str | None = place_info.place_id
        venue_phone: str | None = place_info.phone
        venue_website: str | None = place_info.website
    else:
        # Fall back to Nominatim geocoding when Places API is unavailable
        from app.ingestion.geocoder import geocode  # noqa: PLC0415

        geo_query = city or venue_address or d.get("_location") or ""
        lat, lng = geocode(geo_query) if geo_query else (0.0, 0.0)
        venue_place_id = None
        venue_phone = None
        venue_website = None

    title: str = d.get("title") or ""
    description: str | None = d.get("description")

    return UnifiedEvent(
        source="serpapi",
        external_id=raw.external_id,
        title=title,
        description=description,
        start_at=start_dt,
        end_at=end_dt,
        lat=lat,
        lng=lng,
        venue_name=venue_name,
        venue_address=venue_address,
        city=city,
        venue_place_id=venue_place_id,
        venue_phone=venue_phone,
        venue_website=venue_website,
        category_slug=_infer_category(title, description),
        ticket_url=ticket_url,
        ticket_links=ticket_links,
        price_min=None,  # not available in Google Events results
        price_max=None,
        image_url=image_url,
        raw_payload=d,
    )


# --- Dispatcher -----------------------------------------------------------

_NORMALIZERS = {
    "eventbrite": normalize_eventbrite,
    "meetup": normalize_meetup,
    "serpapi": normalize_serpapi,
}


def normalize(raw: RawEvent) -> UnifiedEvent:
    """Map a `RawEvent` from any source to a `UnifiedEvent`."""
    fn = _NORMALIZERS.get(raw.source)
    if fn is None:
        raise ValueError(f"No normaliser registered for source: {raw.source!r}")
    return fn(raw)
