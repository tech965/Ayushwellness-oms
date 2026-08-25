# AyushWellness OMS — API

FastAPI backend. See the repo root [`README.md`](../../README.md) and
[`docs/architecture/backend.md`](../../docs/architecture/backend.md) for
the full picture; this file only covers commands local to this package.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
.venv/bin/python -m pip install -e ".[dev]"            # macOS/Linux

uvicorn app.main:app --reload   # http://localhost:8000
pytest
ruff check app tests
black app tests
mypy app
alembic upgrade head
```

Requires `DATABASE_URL` and `REDIS_URL` — copy the repo root
`.env.example` to `.env`, or run `docker compose up postgres redis -d`.
