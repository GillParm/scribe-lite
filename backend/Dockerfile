# API image. Build from the repository root so the bundled samples can be copied in:
#   docker build -f backend/Dockerfile -t scribe-lite-api .
# Listens on $PORT (Cloud Run sets 8080). Runs migrations, then uvicorn.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so code changes reuse the cached layer (spaCy model included).
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 scribe \
    && mkdir /data \
    && chown scribe /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY samples ./samples
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    SAMPLES_DIR=/app/samples \
    DATA_DIR=/data \
    PORT=8080
USER scribe
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn scribe.api.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers"]
