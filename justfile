# Task runner. Install `just` with:  winget install Casey.Just
# Every recipe is also a plain `uv run ...` command if you prefer not to install just.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

# Install / update all dependencies into .venv
sync:
    uv sync

# Run the API with autoreload
api:
    uv run uvicorn invoice_ops.api.main:app --reload

# Run a Celery worker (needs a real broker; not used in eager dev mode)
worker:
    uv run celery -A invoice_ops.queue:celery_app worker --loglevel=info

# Lint, format-check, type-check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy src

# Auto-fix lint + format
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Run the test suite
test:
    uv run pytest

# Run the extraction eval harness (needs OPENROUTER_API_KEY + evals/fixtures/*)
eval:
    uv run python evals/run.py

# Apply migrations
migrate:
    uv run alembic upgrade head

# Create a new migration from model changes:  just revision "add invoices table"
revision message:
    uv run alembic revision --autogenerate -m "{{message}}"

# Bring up prod-shaped infra (needs Docker)
infra-up:
    docker compose up -d

infra-down:
    docker compose down
