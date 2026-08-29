# AyushWellness OMS — Celery worker image.
# Build context must be the repo root: `docker build -f docker/worker.Dockerfile .`
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/requirements-lock.txt ./
RUN pip install -r requirements-lock.txt

COPY apps/api/app ./app

# -B: embedded beat (safe: compose runs exactly one worker instance).
# -Q celery,shiprocket + --concurrency: keep Shiprocket's long list
# crawls off the queue that carries Shopify order syncs so neither
# starves the other — see render.yaml and app/workers/celery_app.py.
CMD ["celery", "-A", "app.workers.celery_app", "worker", "-B", "-Q", "celery,shiprocket", "--concurrency=3", "--loglevel=info"]
