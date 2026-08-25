# How to Run This Project

First, go to the project root (this folder):

```
cd "C:\Users\user\Desktop\Ayush oms\ayushwellness-oms\ayushwellness-oms"
```

All commands below assume you start from here.

## 1. Start Postgres + Redis (from project root)

```
docker compose up postgres redis -d
```

## 2. Run the backend

```
cd apps/api
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Backend runs at http://localhost:8000

## 3. Run the frontend

Open a new terminal:

```
cd apps/web
npm run dev
```

Frontend runs at http://localhost:3000

---

### First time only (before step 2 and 3 above work)

```
cd apps/api
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe scripts\seed.py
cd ../web
npm install
```

Also copy `.env.example` → `.env` and `apps/web/.env.example` → `apps/web/.env.local` before running.
