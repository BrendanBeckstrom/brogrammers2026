# One image for the API and the built web app (and later the ingest Job).
# The GitHub Action builds it on every push to main; see deploy/.

# --- Stage 1: build the web app into web/dist ---
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- Stage 2: Python runtime serving the API and web/dist ---
FROM python:3.12-slim
# Pinned to the uv version the team uses locally.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# data/raw and data/chunks.jsonl are kept out by .dockerignore.
COPY api/ ./api/
COPY ingest/ ./ingest/
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist

ARG APP_VERSION=dev
ENV PORT=8080 \
    APP_VERSION=$APP_VERSION
# Numeric, so the pod's runAsNonRoot can check it.
USER 1000
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
