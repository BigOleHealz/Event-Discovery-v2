# Event Discovery — Python Ingestion API

This service is responsible for **event ingestion only**. All user-facing API endpoints (auth, events, invites, contacts) are handled by the Next.js app in `apps/web`.

---

## Responsibilities

| Concern | Owner |
|---|---|
| Event ingestion (SerpApi, Eventbrite, Meetup) | This service (FastAPI + Celery) |
| Deduplication (Qdrant vector search) | This service |
| Database schema / migrations | This service (Alembic) |
| User auth, JWT, OAuth tokens | `apps/web` (NextAuth.js) |
| Event/user API endpoints | `apps/web` (Next.js API routes) |
| SMS invites, Google Contacts | `apps/web` (Next.js API routes) |

---

## Stack

- **Runtime**: Python 3.12
- **Web framework**: FastAPI + Uvicorn
- **ORM**: SQLAlchemy (async) + asyncpg
- **Migrations**: Alembic
- **Task queue**: Celery + Redis broker
- **Scheduler**: Celery Beat (every 6h per source, staggered)
- **Vector DB**: Qdrant (event deduplication via OpenAI embeddings)
- **Geospatial**: PostGIS (`ST_DWithin` for proximity queries)
- **Package manager**: `uv`

---

## Local Development

### Prerequisites

- Docker Desktop running
- Root `.env` file populated (copy from `.env.example`)

### 1 — Start infrastructure

From the **monorepo root**:

```bash
# Postgres (PostGIS), Qdrant, Redis, Neo4j only — no API/worker containers
docker compose up -d postgres qdrant redis neo4j
```

Ports exposed on localhost:

| Service | Port |
|---|---|
| PostgreSQL | `5433` (host) → `5432` (container) |
| Qdrant REST | `6333` |
| Qdrant gRPC | `6334` |
| Redis | `6379` |
| Neo4j HTTP | `7474` |
| Neo4j Bolt | `7687` |

### 2 — Install Python dependencies

```bash
cd apps/api
uv sync
```

### 3 — Run database migrations

```bash
cd apps/api
uv run alembic upgrade head
```

### 4 — Start the API server (dev)

```bash
cd apps/api
uv run uvicorn app.main:app --reload --port 8000
```

### 5 — Start a Celery worker (separate terminal)

```bash
cd apps/api
uv run celery -A app.tasks.celery_app.celery_app worker --loglevel=info
```

### 6 — Start Celery Beat scheduler (separate terminal, optional)

Beat triggers ingestion automatically every 6 hours per source. Skip this if you want to trigger manually via the API.

```bash
cd apps/api
uv run celery -A app.tasks.celery_app.celery_app beat --loglevel=info
```

---

## Docker (full stack)

The `api`, `worker`, and `beat` containers are behind the `full` profile to avoid running them during frontend-only development.

```bash
# From monorepo root — starts everything including API + workers
docker compose --profile full up --build
```

---

## API Endpoints

Base URL: `http://localhost:8000`

All admin endpoints require the `X-Admin-API-Key` header matching `ADMIN_API_KEY` in `.env`.

### Health

```
GET /health
```

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

### Trigger an ingestion run

```
POST /api/v1/admin/ingest/trigger?source=<source>
```

**Query parameters**

| Param | Required | Values |
|---|---|---|
| `source` | yes | `serpapi` |

**Headers**

```
X-Admin-API-Key: <your ADMIN_API_KEY>
```

**Example**

```bash
curl -X POST \
  "http://localhost:8000/api/v1/admin/ingest/trigger?source=serpapi" \
  -H "X-Admin-API-Key: change-me-in-production"
```

**Response**

```json
{
  "task_id": "b3f2e1a0-...",
  "source": "serpapi",
  "queued_at": "2026-03-09T18:00:00Z"
}
```

The task runs asynchronously in the Celery worker. Use the status endpoint to check progress.

---

### Check a specific task

After triggering an ingestion run you get back a `task_id`. Use it to poll the Celery result backend (Redis) directly via the API:

```
GET /api/v1/admin/ingest/task/{task_id}
```

```bash
curl "http://localhost:8000/api/v1/admin/ingest/task/b3f2e1a0-1234-5678-abcd-ef0123456789" \
  -H "X-Admin-API-Key: change-me-in-production"
```

**Response — pending**

```json
{
  "task_id": "b3f2e1a0-...",
  "status": "PENDING",
  "result": null
}
```

**Response — success**

```json
{
  "task_id": "b3f2e1a0-...",
  "status": "SUCCESS",
  "result": {
    "events_fetched": 84,
    "events_new": 12,
    "events_deduped": 3,
    "events_skipped": 5
  }
}
```

**Response — failure**

```json
{
  "task_id": "b3f2e1a0-...",
  "status": "FAILURE",
  "result": "ConnectionError: could not reach SerpApi"
}
```

Possible `status` values: `PENDING` · `STARTED` · `RETRY` · `SUCCESS` · `FAILURE`

---

### Get ingestion status

```
GET /api/v1/admin/ingest/status
```

Returns the most recent ingestion run record for each known source.

```bash
curl "http://localhost:8000/api/v1/admin/ingest/status" \
  -H "X-Admin-API-Key: change-me-in-production"
```

**Response**

```json
{
  "runs": [
    {
      "source": "serpapi",
      "started_at": "2026-03-09T12:45:00Z",
      "finished_at": "2026-03-09T12:46:12Z",
      "events_fetched": 84,
      "events_new": 12,
      "events_deduped": 3,
      "error": null,
      "duration_s": 72.4
    }
  ]
}
```

---

### Interactive docs (debug mode only)

Set `DEBUG=true` in `.env`, then visit:

- Swagger UI: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`

---

## Ingestion Pipeline

```
Celery Beat (every 6h) or manual POST /admin/ingest/trigger
  │
  ▼
run_ingestion(source)           ← Celery task (ingest_task.py)
  │
  ├── ingester.fetch_events()   ← SerpApiIngester / EventbriteIngester / …
  ├── normalizer.normalize()    → UnifiedEvent (or None = skip)
  ├── dedup_service.process()   → embed text → Qdrant similarity search
  │     └── if score ≥ 0.92: set canonical_id
  └── event_service.upsert()    → Postgres ON CONFLICT UPDATE
        └── dedup_service.update_event_id() → back-fill Qdrant payload
```

### Year inference (SerpApi)

Google Events often omits the year. The normalizer applies this logic:

- Assume the current year.
- If the resulting date is **< 6 months in the past** → skip (event already passed).
- If the resulting date is **≥ 6 months in the past** → bump to next year (next annual occurrence).

### Date window guard

Events outside `[Jan 1 this year, Jan 1 year+2)` are dropped as a safety net against malformed upstream dates.

---

## Database Migrations

Migrations live in `migrations/versions/`. Alembic manages schema for all tables **except** `oauth_tokens`, which is owned by Prisma in `apps/web`.

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Check current revision
uv run alembic current

# Generate a new migration after model changes
uv run alembic revision --autogenerate -m "describe_change"

# Roll back one step
uv run alembic downgrade -1
```

### Applied migrations

| Revision | Description |
|---|---|
| `831b8bc4` | Phase 1 init — users, venues, categories, events, invites, user_events |
| `52eb838b` | Ingestion tracking — `ingestion_runs` table |
| `c2857738` | Add `ticket_links` (JSONB) to events |
| `4eccaccb` | Add `venue_phone`, `venue_website` to venues |
| `49359f7f` | Add `venue_lat`, `venue_lng` columns (later dropped) |
| `87e44fca` | Drop `venue_lat`, `venue_lng` (rely on PostGIS `location` only) |
| `e052e6da` | Placeholder — superseded by next revision |
| `36238e87` | Create `oauth_tokens` table (Prisma also references this) |

---

## Environment Variables

All variables are read from the root `.env` file. See `.env.example` for full documentation.

Key variables for this service:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/eventdb` | Async Postgres connection string |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant REST endpoint |
| `QDRANT_API_KEY` | _(empty)_ | Required in cloud-hosted Qdrant |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker + result backend |
| `OPENAI_API_KEY` | _(empty)_ | Required for event deduplication embeddings |
| `SERPAPI_API_KEY` | _(empty)_ | SerpApi key for Google Events scraping |
| `SERPAPI_LOCATIONS` | _(empty)_ | Comma-separated city strings, e.g. `Austin TX,New York NY` |
| `SERPAPI_DATE_FILTER` | `date:week` | SerpApi `htichips` filter |
| `GOOGLE_PLACES_API_KEY` | _(empty)_ | Venue enrichment via Places API |
| `ADMIN_API_KEY` | `change-me-in-production` | Protects `/api/v1/admin/*` endpoints |
| `APP_BASE_URL` | `http://localhost:3000` | CORS allow-origin (set to `NEXTAUTH_URL` value) |
| `DEBUG` | `false` | Enables `/api/docs` and `/api/redoc` |

---

## Running Tests

```bash
cd apps/api

# All tests
uv run pytest

# Specific module
uv run pytest tests/test_ingestion/test_serpapi_normalizer.py -v

# With coverage
uv run pytest --cov=app --cov-report=term-missing
```

Tests use a separate in-process Postgres session. The `conftest.py` fixture drops and recreates the `public` schema between test runs for a clean slate.
