.DEFAULT_GOAL := help
.PHONY: help install dev-api dev-web serve check check-python check-web test build build-web package

help:
	@echo "install       Install locked Python and frontend dependencies"
	@echo "dev-api       Start the backend with reload at http://127.0.0.1:8310"
	@echo "dev-web       Start the frontend with HMR at http://127.0.0.1:5173"
	@echo "serve         Build the console and serve it with the backend"
	@echo "check         Check Python and frontend formatting, lint, and types"
	@echo "test          Run Python tests"
	@echo "build         Build frontend assets and both Python packages"

install:
	uv sync --locked
	pnpm --dir frontend install --frozen-lockfile

dev-api:
	uv run --locked channel-gateway serve --reload

dev-web:
	pnpm --dir frontend --filter @channel-gateway/console dev

serve: build-web
	uv run --locked channel-gateway serve

check: check-python check-web

check-python:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked pyright

check-web:
	pnpm --dir frontend run check

test:
	uv run --locked pytest

build-web:
	pnpm --dir frontend run build

package:
	uv build --all-packages

build: build-web
	$(MAKE) package
