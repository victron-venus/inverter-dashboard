#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked --extra test --with ruff --with pytest-cov python -m ruff check .
uv run --locked --extra test --with ruff --with pytest-cov python -m ruff format --check .
uv run --locked --extra test --with ruff --with pytest-cov python -m pytest --cov=src/inverter_dashboard --cov-report=xml --cov-report=term-missing --cov-fail-under=68
