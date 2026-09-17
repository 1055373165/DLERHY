# syntax=docker/dockerfile:1

# --- frontend build ------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- application -----------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    BOOK_AGENT_FRONTEND_DIST_DIR=/app/frontend/dist

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN pip install --upgrade pip \
    && pip install .

COPY --from=frontend /frontend/dist ./frontend/dist

RUN useradd --system --create-home --uid 10001 bookagent \
    && mkdir -p /app/artifacts/exports /app/artifacts/uploads /app/artifacts/blobs \
       /app/artifacts/parse-ir /app/artifacts/document-images \
    && chown -R bookagent:bookagent /app/artifacts

USER bookagent

EXPOSE 8000

CMD ["uvicorn", "book_agent.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
