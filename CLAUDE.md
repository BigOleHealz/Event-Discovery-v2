# Event Discovery App — Claude Code Development Plan

## Core Stack

- **Frontend**: Next.js (UI scaffolded in V0.dev), deployed on Vercel
- **Backend**: Python (FastAPI), deployed on AWS
- **Primary DB**: PostgreSQL on AWS RDS (PostGIS for geospatial)
- **Vector DB**: Qdrant (event deduplication via semantic embeddings)
- **Graph DB**: Neo4j (Phase 5 — event/user relationship modeling)
- **Auth**: Google OAuth + Apple Sign-In (via NextAuth.js)
- **SMS**: Twilio (event invites)

---

## Pre-Phase: Repository & Monorepo Setup

**Folder Structure**
```
event-discovery/
├── apps/
│   ├── web/                    # Next.js (V0.dev scaffolded)
│   └── api/                    # FastAPI backend
├── packages/
│   ├── openapi/                # Shared OpenAPI spec (source of truth)
│   └── shared-types/           # Generated TypeScript types from spec
├── infra/
│   ├── terraform/              # AWS RDS, SQS, Secrets Manager
│   └── docker/                 # Local dev compose
├── scripts/                    # DB migrations, seed scripts
├── .env.example
└── docker-compose.yml          # Local: Postgres+PostGIS, Qdrant, Redis, Neo4j
```

**Checklist**
- [ ] Init monorepo (pnpm workspaces or Turborepo)
- [ ] `docker-compose.yml` with: `postgres:15-alpine` + PostGIS, Qdrant latest, Redis, Neo4j 5.x
- [ ] `.env.example` with all required keys documented
- [ ] Pre-commit hooks: Black + isort (Python), ESLint + Prettier (TS)
- [ ] GitHub Actions skeleton: lint → test → deploy per workspace

---

## Phase 1 — API Design & Data Modeling

### 1.1 Core Entity Definitions

```
apps/api/app/models/
├── user.py
├── event.py
├── venue.py
├── category.py
└── invite.py
```

### 1.2 PostgreSQL Schema (PostGIS)

```sql
-- migrations/001_init.sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- fuzzy text search

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    name          TEXT,
    avatar_url    TEXT,
    oauth_provider TEXT NOT NULL,        -- 'google' | 'apple'
    oauth_sub     TEXT NOT NULL,
    spotify_token JSONB,                 -- encrypted, nullable
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(oauth_provider, oauth_sub)
);

CREATE TABLE venues (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    address    TEXT,
    location   GEOGRAPHY(POINT, 4326) NOT NULL,  -- PostGIS
    place_id   TEXT,                   -- Google Places ID for dedup
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX venues_location_idx ON venues USING GIST(location);

CREATE TABLE categories (
    id    SMALLSERIAL PRIMARY KEY,
    slug  TEXT UNIQUE NOT NULL,         -- 'music', 'tech', 'sports'
    label TEXT NOT NULL
);

CREATE TABLE events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id  TEXT,               -- source platform ID
    source       TEXT,               -- 'eventbrite' | 'meetup' | 'facebook'
    canonical_id UUID REFERENCES events(id),  -- points to dedup winner
    title        TEXT NOT NULL,
    description  TEXT,
    start_at     TIMESTAMPTZ NOT NULL,
    end_at       TIMESTAMPTZ,
    venue_id     UUID REFERENCES venues(id),
    category_id  SMALLINT REFERENCES categories(id),
    ticket_url   TEXT,
    price_min    NUMERIC(10,2),
    price_max    NUMERIC(10,2),
    image_url    TEXT,
    raw_payload  JSONB,              -- original source data
    embedding_id TEXT,              -- Qdrant point ID
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE(source, external_id)
);
CREATE INDEX events_start_at_idx ON events(start_at);
CREATE INDEX events_venue_id_idx ON events(venue_id);

CREATE TABLE invites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID REFERENCES events(id) NOT NULL,
    sender_id       UUID REFERENCES users(id) NOT NULL,
    recipient_phone TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'delivered' | 'opened'
    twilio_sid      TEXT,
    deep_link       TEXT NOT NULL,
    sent_at         TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_events (                -- RSVPs, saves, views
    user_id    UUID REFERENCES users(id),
    event_id   UUID REFERENCES events(id),
    action     TEXT NOT NULL,           -- 'view' | 'save' | 'rsvp' | 'invite_sent'
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, event_id, action)
);
```

### 1.3 Qdrant Collection Schema

```python
COLLECTION_NAME = "events"
VECTOR_SIZE = 1536          # text-embedding-3-small
DISTANCE = "Cosine"

# Payload fields stored per point:
# {
#   "event_id": str (UUID),
#   "source": str,
#   "external_id": str,
#   "title": str,
#   "start_at": int (unix timestamp),
#   "lat": float,
#   "lng": float,
# }
```

**Embedding text template** (what gets embedded):
```
{title} | {category} | {venue_name} | {city} | {date_YYYY-MM-DD} | {description[:500]}
```

### 1.4 OpenAPI Spec (source of truth)

```
packages/openapi/
├── openapi.yaml                # Hand-authored spec
└── generated/
    ├── typescript-client/      # Auto-generated via openapi-generator
    └── python-models/          # Auto-generated Pydantic models
```

**Base URL structure:** `/api/v1/{resource}`

### 1.5 Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/events` | List/search events (geo, date, category filters) |
| `GET` | `/events/{id}` | Single event detail |
| `GET` | `/events/nearby` | Events within radius (lat, lng, km) |
| `POST` | `/events` | Create event (admin/ingestion only) |
| `GET` | `/users/me` | Current user profile |
| `PATCH` | `/users/me` | Update preferences |
| `POST` | `/user-events` | Log user action (view/save/rsvp) |
| `GET` | `/users/me/saved` | User's saved events |
| `POST` | `/invites` | Send SMS invite |
| `GET` | `/invites/{id}` | Invite status (for deep link landing) |

**`GET /events` query params:**
```
lat, lng, radius_km (default 25)
start_after, end_before (ISO datetime)
category_slugs (comma-separated)
q (text search)
limit (default 20, max 100)
cursor (pagination)
```

**`GET /events` response shape:**
```json
{
  "data": [{
    "id": "uuid",
    "title": "string",
    "description": "string",
    "start_at": "2026-03-15T19:00:00Z",
    "end_at": "2026-03-15T22:00:00Z",
    "venue": { "id": "uuid", "name": "string", "address": "string", "lat": 37.77, "lng": -122.41 },
    "category": { "slug": "music", "label": "Music" },
    "price_min": 0,
    "price_max": 25,
    "image_url": "string",
    "ticket_url": "string",
    "distance_km": 3.2
  }],
  "cursor": "opaque_string",
  "total": 142
}
```

### Checklist
- [ ] Write `packages/openapi/openapi.yaml` covering all Phase 1 endpoints with full request/response schemas
- [ ] Run `openapi-generator` → TypeScript client into `packages/openapi/generated/typescript-client/`
- [ ] Run `datamodel-code-generator` → Pydantic v2 models into `packages/openapi/generated/python-models/`
- [ ] Write and apply `migrations/001_init.sql` via Alembic
- [ ] Create Qdrant collection via `scripts/init_qdrant.py`
- [ ] Set up MSW (Mock Service Worker) in Next.js dev build for immediate frontend mocking

### Decisions / Tradeoffs
- **GraphQL vs REST**: REST chosen for simplicity and V0.dev compatibility. Revisit in Phase 5 if graph traversal queries become expensive.
- **`canonical_id` self-reference**: All event variants stored, one marked canonical. Frontend always filters `WHERE canonical_id IS NULL`. Keeps audit trail vs. deleting duplicates.
- **Embedding model**: `text-embedding-3-small` (OpenAI) for cost efficiency. Qdrant schema is model-agnostic — swap later if needed by matching vector size.

---

## Phase 2 — Backend: Event Ingestion Pipeline

### File Structure Changes

```
apps/api/
├── app/
│   ├── ingestion/
│   │   ├── base.py             # Abstract BaseIngester
│   │   ├── eventbrite.py
│   │   ├── meetup.py
│   │   ├── facebook.py
│   │   └── normalizer.py       # → unified Event schema
│   ├── dedup/
│   │   ├── embedder.py         # Text → vector via OpenAI
│   │   └── dedup_service.py    # Qdrant similarity search + merge logic
│   ├── tasks/
│   │   ├── celery_app.py
│   │   └── ingest_task.py
│   └── services/
│       ├── event_service.py
│       └── qdrant_service.py
└── workers/
    └── Dockerfile              # Celery worker image
```

### Ingestion Architecture

```
Scheduler (Celery Beat / AWS EventBridge)
    │
    ▼
ingest_task(source="eventbrite", params={...})
    │
    ├── BaseIngester.fetch_events() → [RawEvent]
    ├── normalizer.normalize() → [UnifiedEvent]
    ├── dedup_service.process(event)
    │       ├── embedder.embed(text)          # generate vector
    │       ├── qdrant.search(vector, threshold=0.92)  # find near-duplicates
    │       ├── if duplicate: mark canonical_id
    │       └── qdrant.upsert(point)          # idempotent by external_id
    └── event_service.upsert(event)           # RDS
```

### Normalizer Output Schema (Pydantic)

```python
class UnifiedEvent(BaseModel):
    source: str
    external_id: str
    title: str
    description: str | None
    start_at: datetime
    end_at: datetime | None
    lat: float
    lng: float
    venue_name: str | None
    venue_address: str | None
    category_slug: str | None
    ticket_url: str | None
    price_min: Decimal | None
    price_max: Decimal | None
    image_url: str | None
    raw_payload: dict
```

### Deduplication Logic

```python
SIMILARITY_THRESHOLD = 0.92  # tune per source

async def process(event: UnifiedEvent) -> str:
    text = build_embed_text(event)
    vector = await embedder.embed(text)

    # Pre-filter by ±3 day window before vector search
    results = qdrant.search(
        collection_name="events",
        query_vector=vector,
        query_filter=Filter(must=[
            FieldCondition("start_at", range=DateRange(gte=ts-3d, lte=ts+3d))
        ]),
        limit=5,
        score_threshold=SIMILARITY_THRESHOLD,
    )

    if results:
        event.canonical_id = results[0].payload["event_id"]

    qdrant.upsert(points=[PointStruct(
        id=make_qdrant_id(event.source, event.external_id),  # deterministic
        vector=vector,
        payload={...}
    )])
```

**Idempotency guarantee:** Check `WHERE source=? AND external_id=?` in RDS before embedding. If exists, skip embedding and only update mutable fields. Qdrant point ID is a deterministic UUID5 from `{source}:{external_id}`.

### New API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/admin/ingest/trigger` | API key | Manually trigger ingestion run |
| `GET` | `/admin/ingest/status` | API key | Queue depth, last run stats |

### DB Migration

```sql
-- migrations/002_ingestion_tracking.sql
CREATE TABLE ingestion_runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source         TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    events_fetched INT DEFAULT 0,
    events_new     INT DEFAULT 0,
    events_deduped INT DEFAULT 0,
    error          TEXT
);
```

### Checklist
- [ ] `BaseIngester` ABC with `fetch_events(since: datetime) -> list[RawEvent]`
- [ ] Eventbrite ingester (Eventbrite API v3, OAuth2 app token)
- [ ] Meetup ingester (GraphQL API)
- [ ] Facebook ingester (note: heavily rate-limited — see tradeoffs)
- [ ] `normalizer.py` — map each source schema to `UnifiedEvent`
- [ ] `embedder.py` — OpenAI `text-embedding-3-small`, batched (up to 2048 texts/call)
- [ ] `dedup_service.py` — similarity search with date/geo pre-filter
- [ ] Celery app with Redis broker + Celery Beat (every 6h per source)
- [ ] `event_service.upsert()` — `ON CONFLICT (source, external_id) DO UPDATE`
- [ ] Rate limiting: per-source request throttling with exponential backoff
- [ ] `migrations/002_ingestion_tracking.sql`
- [ ] Unit tests: normalizer + dedup threshold validation with fixture events

### Decisions / Tradeoffs
- **Celery vs SQS**: Celery + Redis for local dev parity. In prod, swap broker to SQS via `kombu` — same task code, different URL.
- **Dedup threshold 0.92**: Start conservative. Manually review flagged pairs before lowering. Too low = legitimate events merged.
- **Facebook Events**: Graph API access for public events requires app review. Plan B: integrate PredictHQ or SeatGeek API as Facebook fallback.

---

## Phase 3 — Auth, Contacts & Social

### File Structure Changes

```
apps/web/
├── app/
│   └── api/auth/[...nextauth]/route.ts
├── lib/
│   ├── auth.ts                 # NextAuth config
│   └── api-client.ts           # Wraps generated OpenAPI client, injects JWT

apps/api/
├── app/
│   ├── auth/
│   │   ├── jwt.py              # Verify Google/Apple JWTs
│   │   └── dependencies.py     # FastAPI Depends(get_current_user)
│   ├── contacts/
│   │   ├── google_contacts.py
│   │   └── contacts_router.py
│   └── invites/
│       ├── twilio_service.py
│       └── invites_router.py
```

### Auth Flow

```
Browser → NextAuth redirect (Google OAuth) → exchange code
       → NextAuth session created (idToken stored)
       → idToken sent to FastAPI POST /auth/verify
       → FastAPI verifies JWT signature (Google JWKS)
       → upsert user in RDS
       → return user + app access token
       → set httpOnly cookie
```

```typescript
// apps/web/lib/auth.ts
export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({ clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET }),
    AppleProvider({ clientId: env.APPLE_CLIENT_ID, clientSecret: env.APPLE_CLIENT_SECRET }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      if (account) token.idToken = account.id_token;
      return token;
    },
    async session({ session, token }) {
      session.idToken = token.idToken;
      return session;
    },
  },
};
```

### Twilio SMS Invite Flow

```python
# apps/api/app/invites/twilio_service.py
async def send_invite(invite: InviteCreate, sender: User, db: AsyncSession):
    event = await get_event(db, invite.event_id)
    deep_link = f"https://app.example.com/events/{event.id}?ref=invite&from={sender.id}"

    message = client.messages.create(
        body=f"{sender.name} invited you to {event.title} on {event.start_at:%b %d}! {deep_link}",
        from_=settings.TWILIO_PHONE,
        to=invite.recipient_phone,
    )
    # store invite record with twilio_sid for status tracking
```

### New API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/verify` | Public | Backend verifies idToken, returns user |
| `GET` | `/contacts/google` | User JWT | Fetch Google contacts (name + phone) |
| `POST` | `/invites` | User JWT | Send Twilio SMS invite |
| `GET` | `/invites/{id}` | Public | Deep link landing — get event from invite |
| `POST` | `/invites/status` | Twilio webhook | Update invite delivery status |

### Rate Limiting

```python
# slowapi (FastAPI-native, backed by Redis)
@router.post("/invites")
@limiter.limit("10/hour")   # per user

@router.post("/admin/ingest/trigger")
@limiter.limit("5/minute")  # per API key
```

### DB Migration

```sql
-- migrations/003_auth_tokens.sql
CREATE TABLE oauth_tokens (
    user_id       UUID REFERENCES users(id) PRIMARY KEY,
    provider      TEXT NOT NULL,
    access_token  TEXT NOT NULL,   -- encrypted via AWS KMS
    refresh_token TEXT,
    expires_at    TIMESTAMPTZ,
    scopes        TEXT[]
);
```

### Checklist
- [ ] NextAuth.js — configure Google + Apple providers in `apps/web`
- [ ] `apps/api/app/auth/jwt.py`: verify Google JWKS + Apple public keys
- [ ] `POST /auth/verify` endpoint — upsert user, return app JWT
- [ ] `Depends(get_current_user)` applied to all protected routers
- [ ] Google Contacts API integration (`people.connections.list`, `contacts.readonly` scope)
- [ ] Twilio SDK: `send_invite()` service + `POST /invites` endpoint
- [ ] Deep link landing: `GET /invites/{id}` → return event pre-loaded
- [ ] `slowapi` rate limiting middleware wired to Redis
- [ ] `migrations/003_auth_tokens.sql`
- [ ] Twilio status webhook: `POST /invites/status` to update `invites.status`

### Decisions / Tradeoffs
- **Apple Sign-In client secret**: Requires a JWT signed with ES256 private key, regenerated every 6 months. Automate rotation via AWS Secrets Manager + Lambda.
- **Google Contacts scope**: Requires OAuth consent screen verification — budget 1-2 weeks for Google review. Build as opt-in, not required.
- **Deep links**: Use Universal Links (iOS) + App Links (Android) for native app. For web-first, a `/join` landing page that renders the event and prompts app install is sufficient.

---

## Phase 4 — Frontend Integration

### File Structure Changes

```
apps/web/
├── app/
│   ├── (map)/
│   │   ├── layout.tsx          # Map shell layout
│   │   └── page.tsx            # Map view root
│   ├── events/[id]/page.tsx
│   └── invite/[inviteId]/page.tsx
├── components/
│   ├── map/
│   │   ├── EventMap.tsx        # Mapbox GL wrapper
│   │   ├── EventMarker.tsx     # Map pin + popover
│   │   └── ClusterLayer.tsx    # Supercluster for dense areas
│   ├── events/
│   │   ├── EventCard.tsx
│   │   ├── EventDetail.tsx
│   │   └── EventFilters.tsx    # Category, date, distance
│   └── invite/
│       └── InviteModal.tsx     # Contact picker + SMS send
├── hooks/
│   ├── useEvents.ts            # SWR + /events geo query
│   ├── useMapBounds.ts         # Track map viewport → re-query
│   └── useInvite.ts
└── public/msw/                 # MSW service worker for API mocking
```

### Map Query Strategy

```typescript
// hooks/useMapBounds.ts
const { data: events } = useSWR(
  bounds ? ['/events/nearby', bounds, filters] : null,
  ([_, bounds, filters]) => apiClient.getEventsNearby({
    lat: bounds.center.lat,
    lng: bounds.center.lng,
    radius_km: boundsToRadius(bounds),
    ...filters,
  }),
  { dedupingInterval: 2000 }  // debounce rapid pan
);
```

### New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/events/nearby` | Map-optimized endpoint with required lat/lng/radius |
| `POST` | `/user-events` | Log user action (view, save, rsvp) |
| `GET` | `/users/me/saved` | Saved events list |

**`GET /events/nearby` response addition** — include GeoJSON for direct Mapbox source injection:
```json
{
  "data": [...],
  "geojson": {
    "type": "FeatureCollection",
    "features": [{ "type": "Feature", "geometry": {...}, "properties": {...} }]
  }
}
```

### DB Migration

```sql
-- migrations/004_geospatial_indexes.sql
-- Partial index for canonical events only
CREATE INDEX events_canonical_location_idx
    ON events USING GIST(
        (SELECT location FROM venues WHERE venues.id = events.venue_id)
    )
    WHERE canonical_id IS NULL;

-- Compound index for common filter pattern
CREATE INDEX events_start_category_idx ON events(start_at, category_id)
    WHERE canonical_id IS NULL;
```

### Checklist
- [ ] Mapbox GL JS install + `EventMap.tsx` with basic tile layer
- [ ] `ClusterLayer.tsx` using Supercluster — clusters collapse on zoom out, expand on click
- [ ] `EventMarker.tsx` — hover shows mini card, click opens `EventDetail` sheet
- [ ] `EventFilters.tsx` — category chips, date range picker, distance slider
- [ ] `useMapBounds.ts` — debounced SWR re-fetch on map move
- [ ] `EventCard.tsx` — used in both list sidebar and map popover
- [ ] `InviteModal.tsx` — contact search, multi-select, send via `POST /invites`
- [ ] Auth: wrap app in NextAuth `SessionProvider`, inject `idToken` into API client headers
- [ ] MSW handlers for all Phase 1–3 endpoints
- [ ] Mobile-responsive: map full-screen, bottom sheet for event detail
- [ ] `migrations/004_geospatial_indexes.sql`

### Decisions / Tradeoffs
- **Mapbox vs Google Maps**: Mapbox GL chosen — better performance with custom layers, vector tiles. Google Maps still needed for Places API (venue dedup). Maintain both API keys.
- **GeoJSON endpoint vs client-side transform**: Returning GeoJSON directly avoids a client-side loop over 100+ events on every re-render. Worth the backend overhead.
- **Map-first vs list-first mobile UX**: Recommend map-first (full screen) with drag-up bottom sheet for list. Flag to designer if V0.dev scaffold assumed list-first.

---

## Phase 5 — Recommendations (Spotify + User Preference Model)

### File Structure Changes

```
apps/api/
└── app/
    └── recommendations/
        ├── spotify_service.py   # Fetch top genres/artists
        ├── preference_model.py  # Collaborative filter
        ├── neo4j_service.py     # Graph queries
        └── recs_router.py
```

### Spotify Integration Flow

```
User connects Spotify → OAuth2 PKCE → store encrypted tokens
→ background task: pull top_artists (6mo) + genres
→ store as users.spotify_preferences JSONB
→ weight music/concert events: score += 0.3 if event.category matches genre
```

### User Preference Model

```python
# Implicit signal weights
WEIGHTS = { "view": 1, "save": 3, "rsvp": 5, "invite_sent": 4 }

async def build_user_vector(user_id: UUID, db: AsyncSession) -> dict:
    """Returns {category_slug: score} preference vector"""
    rows = await db.execute(
        select(UserEvent.action, Category.slug, func.count())
        .join(Event).join(Category)
        .where(UserEvent.user_id == user_id)
        .where(UserEvent.created_at > datetime.now() - timedelta(days=90))
        .group_by(UserEvent.action, Category.slug)
    )
    scores = defaultdict(float)
    for action, slug, count in rows:
        scores[slug] += WEIGHTS[action] * count
    return dict(scores)
```

### Neo4j Graph Schema

```cypher
// Nodes
(:User {id, name})
(:Event {id, title, start_at, category})
(:Venue {id, name})

// Relationships
(:User)-[:ATTENDED {at: datetime}]->(:Event)
(:User)-[:SAVED]->(:Event)
(:User)-[:FRIEND_OF {since: datetime}]->(:User)
(:Event)-[:SIMILAR_TO {score: float}]->(:Event)
(:User)-[:INVITED {sent_at: datetime}]->(:User)

// Graph recommendation query:
MATCH (me:User {id: $user_id})-[:FRIEND_OF]-(friend)-[:ATTENDED|SAVED]->(e:Event)
WHERE e.start_at > datetime() AND NOT (me)-[:ATTENDED|SAVED]->(e)
RETURN e, count(friend) AS friend_count
ORDER BY friend_count DESC LIMIT 20
```

### New API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/recommendations` | User JWT | Personalized event feed |
| `POST` | `/auth/spotify` | User JWT | Connect Spotify account |
| `DELETE` | `/auth/spotify` | User JWT | Disconnect Spotify |
| `GET` | `/events/{id}/similar` | Public | Similar events (Qdrant + Neo4j) |
| `GET` | `/users/me/friends` | User JWT | Friends attending events |

**`GET /recommendations` response** — same shape as `GET /events` plus:
```json
{ "score": 0.87, "reason": "friends_attending | category_match | spotify_genre | trending_nearby" }
```

### Recommendation Blend Order
1. Neo4j: friends attending/saved
2. Spotify: genre-matched events
3. Preference vector: implicit category signals
4. Proximity fallback: trending nearby

**Cold start**: Explicit onboarding category selection (3 taps) → location → time-of-week → Spotify genres.

### DB Migration

```sql
-- migrations/005_recommendations.sql
ALTER TABLE users ADD COLUMN spotify_preferences JSONB;
ALTER TABLE users ADD COLUMN preference_vector JSONB;
ALTER TABLE users ADD COLUMN preference_updated_at TIMESTAMPTZ;

CREATE INDEX user_events_user_id_idx ON user_events(user_id, created_at DESC);
```

### Checklist
- [ ] Spotify OAuth2 PKCE in Next.js, token storage in `oauth_tokens`
- [ ] `spotify_service.py` — pull top artists/genres, store in `users.spotify_preferences`
- [ ] `preference_model.py` — implicit signal aggregation from `user_events`
- [ ] Celery task: rebuild preference vectors nightly
- [ ] Neo4j driver install + `neo4j_service.py`
- [ ] Neo4j Cypher constraints + indexes on `User.id`, `Event.id`
- [ ] Sync writes to Neo4j on: RSVP, save, invite sent, friendship
- [ ] `GET /recommendations` endpoint blending all signal sources
- [ ] `GET /events/{id}/similar` — Qdrant ANN search by `embedding_id`
- [ ] `migrations/005_recommendations.sql`

### Decisions / Tradeoffs
- **Neo4j hosting**: AuraDB Free for dev, AuraDB Professional for prod. Self-hosted EC2 cheaper at scale but adds ops overhead.
- **Collaborative filter vs LLM reranking**: Simple weighted category scores first. If recommendation CTR < 15%, consider passing top 50 candidates to Claude API for reranking — adds latency.
- **Cold start**: Explicit onboarding category selection dramatically improves cold start. Build before launching recommendations.

---

## Cross-Cutting Concerns

### Auth Middleware Pattern

```python
# Every protected router:
router = APIRouter(prefix="/events", dependencies=[Depends(get_current_user)])

# Public routes explicitly opt out:
@router.get("/{id}", dependencies=[])
```

### PostGIS Query Pattern (always use `ST_DWithin`, never `ST_Distance` in WHERE)

```python
stmt = (
    select(Event, Venue,
           func.ST_Distance(Venue.location, point).label("distance_m"))
    .join(Venue)
    .where(func.ST_DWithin(Venue.location, point, radius_m))
    .where(Event.canonical_id.is_(None))
    .order_by("distance_m")
)
```

### Qdrant Idempotency Pattern

```python
def make_qdrant_id(source: str, external_id: str) -> str:
    return str(uuid5(NAMESPACE_DNS, f"{source}:{external_id}"))

# Always use upsert, never insert
qdrant_client.upsert(collection_name="events", points=[
    PointStruct(id=make_qdrant_id(source, external_id), vector=vector, payload=payload)
])
```

### Environment Config

```bash
# .env.example
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/eventdb
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEXTAUTH_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
APPLE_CLIENT_ID=
APPLE_CLIENT_SECRET=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
NEXT_PUBLIC_MAPBOX_TOKEN=
EVENTBRITE_API_KEY=
MEETUP_API_KEY=
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=
```

**Production:** All secrets fetched from AWS Secrets Manager at startup via `aws-secretsmanager-caching-python` (1h cache, auto-rotates). No secrets in environment variables in prod.

---

## Recommended Build Order (within each phase)

Follow this sequence to prevent frontend/backend drift:

1. Update `openapi.yaml` with new endpoints/schemas
2. Regenerate TypeScript client + Pydantic models
3. Add MSW mock handlers for new endpoints (frontend unblocked immediately)
4. Write DB migration → apply locally → test
5. Implement FastAPI endpoint (uses generated Pydantic models)
6. Write integration test against local Postgres/Qdrant
7. Implement Next.js component/hook consuming generated TypeScript client
8. E2E test against real local backend
9. Deploy backend to AWS staging
10. Deploy frontend to Vercel preview

The OpenAPI spec is the contract enforced by both sides — V0.dev components never need to guess at shapes, and the backend never drifts from what the frontend expects.
