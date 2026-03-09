FROM python:3.12-slim

# Copy uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps first (better layer caching — only re-runs on lockfile change)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

# Put the venv on PATH so uvicorn and other scripts are found
ENV PATH="/app/.venv/bin:$PATH"

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
