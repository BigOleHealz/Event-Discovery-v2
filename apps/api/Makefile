.PHONY: install dev lint format test migrate

# After install, activate the venv:
#   source .venv/bin/activate
# Then you can run uvicorn, alembic, pytest etc. directly without 'uv run'.

install:
	uv sync --extra dev

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

lint:
	uv run ruff check app tests
	uv run mypy app

format:
	uv run ruff format app tests

test:
	uv run pytest

migrate:
	uv run alembic upgrade head

migrate-new:
	uv run alembic revision --autogenerate -m "$(name)"
