"""Live smoke test for the OMS AI Assistant.

Runs the canonical natural-language questions against a REAL database and
a REAL Groq call, printing the answer, the tools it used, its data
sources and the latency for each. Nothing is written except a
`chat_query_logs` row per question (same as a normal request).

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql+asyncpg://.../ayushwellness_oms"
    $env:GROQ_API_KEY  = "gsk_..."
    .venv\\Scripts\\python.exe scripts\\chat_smoke.py
    .venv\\Scripts\\python.exe scripts\\chat_smoke.py --user ops@ayushwellness-oms.com
    .venv\\Scripts\\python.exe scripts\\chat_smoke.py --ask "How many COD orders today?"

Exit code is non-zero if any question came back not-ok.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.auth import User
from app.repositories.auth import UserRepository
from sqlalchemy import select

QUESTIONS: list[str] = [
    "How many orders did we receive today?",
    "What is today's revenue?",
    "How many orders were COD today?",
    "How many prepaid orders did we receive today?",
    "What was yesterday's revenue?",
    "Show me today's cancelled orders.",
    "How many orders are pending fulfillment?",
    "How many orders were delivered today?",
    "What is our RTO count this month?",
    "Which products sold the most today?",
    "Give me today's top 5 products.",
    "How many orders are currently in transit?",
    "How many open NDR orders do we have?",
    "How many returns happened this week?",
    "Compare today's revenue with yesterday.",
    "Compare this week's orders with last week.",
    "What is our COD percentage today?",
    "What is our prepaid percentage today?",
    "Which courier has the highest RTO this month?",
    "Which courier has the highest number of delayed shipments?",
    "Give me a summary of today's operations.",
    "Give me the most important problems I should look at today.",
]


async def _pick_user(session, email: str | None) -> User | None:
    repo = UserRepository(session)
    if email:
        user = await repo.get_by_email(email)
        return await repo.get_with_permissions(user.id) if user else None
    row = await session.execute(
        select(User).where(User.is_superuser.is_(True), User.is_active.is_(True)).limit(1)
    )
    user = row.scalar_one_or_none()
    if user is None:
        row = await session.execute(select(User).where(User.is_active.is_(True)).limit(1))
        user = row.scalar_one_or_none()
    return await repo.get_with_permissions(user.id) if user else None


async def _run(email: str | None, questions: list[str]) -> int:
    if not settings.GROQ_API_KEY:
        print("GROQ_API_KEY is not set — nothing to smoke-test.", file=sys.stderr)
        return 2

    # Imported here so `--help` works even if optional deps shift.
    from app.chat.service import ChatService

    failures = 0
    async with AsyncSessionLocal() as session:
        user = await _pick_user(session, email)
        if user is None:
            print("No usable user found in this database.", file=sys.stderr)
            return 2
        print(f"Model: {settings.CHAT_LLM_MODEL}   User: {user.email}   Q: {len(questions)}\n")

        for i, question in enumerate(questions, 1):
            result = await ChatService(session, user).answer(question)
            status = "ok " if result.ok else "FAIL"
            if not result.ok:
                failures += 1
            print(f"[{i:>2}/{len(questions)}] {status}  {question}")
            print(f"      tools:   {', '.join(result.tools_used) or '—'}")
            print(f"      sources: {', '.join(result.sources) or '—'}")
            if result.partial:
                print("      (partial data)")
            answer = result.answer.replace("\n", "\n      ")
            print(f"      → {answer}\n")

    await engine.dispose()
    print(f"Done. {failures} failure(s).")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="Email of the OMS user to run as (default: any superuser).")
    parser.add_argument("--ask", action="append", help="Ask a specific question (repeatable).")
    args = parser.parse_args()

    questions = args.ask if args.ask else QUESTIONS
    raise SystemExit(asyncio.run(_run(args.user, questions)))


if __name__ == "__main__":
    main()
