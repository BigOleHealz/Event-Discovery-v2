# Celery worker image — shares the same base as the API Dockerfile.
# Build context: apps/api/
#
# Usage (via docker-compose --profile full):
#   celery worker:  celery -A app.tasks.celery_app.celery_app worker --loglevel=info
#   celery beat:    celery -A app.tasks.celery_app.celery_app beat  --loglevel=info

FROM python:3.12-slim

# uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Copy only source — not the local .venv (would overwrite the Linux venv above)
COPY app/ ./app/
COPY alembic.ini ./

# Default command: Celery worker.
# Override in docker-compose for the Beat scheduler.
CMD ["celery", "-A", "app.tasks.celery_app.celery_app", "worker", "--loglevel=info", "--concurrency=2"]
