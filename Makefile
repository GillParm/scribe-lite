# Common tasks. Run from the repository root.
.PHONY: hooks db api web test lint typecheck fmt samples eval down

hooks:            ## enable the repo's pre-commit hook
	git config core.hooksPath .githooks

db:               ## start Postgres on localhost:5434
	docker compose up -d db

api:              ## run the API on :8400 with reload
	cd backend && uv run alembic upgrade head && uv run uvicorn scribe.api.main:app --reload --port 8400

web:              ## run the web app on :3400
	cd frontend && npm run dev

test:             ## backend tests (offline)
	cd backend && uv run pytest

lint:             ## ruff + eslint + prettier
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npm run format:check

typecheck:        ## strict mypy + tsc
	cd backend && uv run mypy
	cd frontend && npm run typecheck

fmt:              ## format everything
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd frontend && npm run format

samples:          ## re-voice the bundled samples (macOS only)
	cd backend && uv run python ../samples/tools/build_audio.py

eval:             ## score the sample notes against the reference facts
	cd backend && uv run python ../eval/run_eval.py

down:             ## stop containers
	docker compose down
