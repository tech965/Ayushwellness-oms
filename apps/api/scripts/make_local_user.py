"""Create (or reset the password of) a local superuser for testing.

A superuser bypasses every permission check, including `chat.use`, so
this is the quickest way to get a login that can use the AI assistant
against a local SQLite `DATABASE_URL`. For a realistic RBAC setup use
`scripts/seed.py` against Postgres instead.

Usage (from apps/api):
    .venv\\Scripts\\python.exe scripts\\make_local_user.py admin@example.com Admin@12345
"""

from __future__ import annotations

import asyncio
import sys

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.models.auth import User
from sqlalchemy import select


async def _run(email: str, password: str) -> None:
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            session.add(
                User(
                    name="Local Admin",
                    email=email,
                    password_hash=hash_password(password),
                    is_active=True,
                    is_superuser=True,
                )
            )
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
            user.is_superuser = True
            action = "updated"
        await session.commit()
    await engine.dispose()
    print(f"{action} superuser {email}")


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(_run(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
