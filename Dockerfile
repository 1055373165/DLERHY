FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN pip install --upgrade pip \
    && pip install -e .

RUN mkdir -p /app/artifacts/exports

EXPOSE 8000

CMD ["uvicorn", "book_agent.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
