"""One-off admin-account fix — NOT part of the app, no code changes.

Production incident this fixes: the shared admin account had the
`TEAM_LEADER` role attached (in addition to `ADMIN`) -- `RoleRedirect`
(apps/web/components/layout/role-redirect.tsx) treats any TEAM_LEADER-
holding user as confined to `/team/*`, so logging in as "admin" landed on
`/team/dashboard` instead of the real admin dashboard -- and, since that
same redirect fires on every navigation attempt, the account couldn't
even reach `/users` in the browser to fix its own roles. This script
fixes it at the data layer instead, using ONLY the existing
`UserService`/`RoleRepository` -- no raw SQL, no schema change.

What it does:
1. Strips `TEAM_LEADER`/`TELECALLER` off the given admin account's roles
   (every OTHER role that account already holds -- e.g. `ADMIN` -- is
   preserved untouched; this is a full role_ids replacement under the
   hood, so the current set is read first and only those two names are
   removed from it, never a blind reset).
2. Creates a brand-new user with the given email/password and the
   `TELECALLER` role -- a real, separate account for telecaller testing/
   demo use, never reusing the admin credential.

Both steps require the relevant `Role` rows (`TEAM_LEADER`/`TELECALLER`)
to already exist -- this script never invents/creates a role with
guessed permissions; if `TELECALLER` doesn't exist yet, create it first
via the Roles page (or `RBACService.create_role`) with whatever
permissions telecallers should actually have, then re-run this.

Run in a Render Shell (API or worker service -- both have DB access):
    python scripts/fix_admin_role_and_create_telecaller.py \\
        --admin-email admin@ayushwellness-oms.com \\
        --telecaller-email telecaller@ayushwellness-oms.com \\
        --telecaller-password "SomeStrongPassword123!" \\
        --telecaller-name "Demo Telecaller"

Add --dry-run first to see current state and what would change with no
writes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import ConflictError, NotFoundError  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.repositories.auth import UserRepository  # noqa: E402
from app.repositories.rbac import RoleRepository  # noqa: E402
from app.services.user_service import UserService  # noqa: E402

_ROLES_TO_STRIP_FROM_ADMIN = ("TEAM_LEADER", "TELECALLER")


async def _run(
    *,
    admin_email: str,
    telecaller_email: str,
    telecaller_password: str,
    telecaller_name: str,
    dry_run: bool,
) -> None:
    async with AsyncSessionLocal() as session:
        users = UserRepository(session)
        roles = RoleRepository(session)
        user_service = UserService(session)

        admin_user = await users.get_by_email(admin_email)
        if admin_user is None:
            raise SystemExit(f"No user found with email {admin_email!r}.")

        current_role_names = sorted(admin_user.role_names)
        current_role_ids = {ur.role_id: ur.role.name for ur in admin_user.user_roles}
        print(f"--- {admin_email} ---")
        print(f"id={admin_user.id} is_superuser={admin_user.is_superuser}")
        print(f"current roles: {current_role_names}")

        strip_ids = {
            role_id
            for role_id, name in current_role_ids.items()
            if name in _ROLES_TO_STRIP_FROM_ADMIN
        }
        if not strip_ids:
            print(
                "Already has neither TEAM_LEADER nor TELECALLER -- nothing to change "
                "on this account."
            )
        else:
            new_role_ids = [rid for rid in current_role_ids if rid not in strip_ids]
            removed = [current_role_ids[rid] for rid in strip_ids]
            print(f"will remove: {removed}  (every other existing role is kept as-is)")
            if not dry_run:
                await user_service.update_user(admin_user.id, role_ids=new_role_ids)
                print("done -- roles updated.")

        print(f"\n--- create telecaller account: {telecaller_email} ---")
        telecaller_role = await roles.get_by_name("TELECALLER")
        if telecaller_role is None:
            raise SystemExit(
                "No Role named 'TELECALLER' exists -- create it first via the Roles "
                "page (Administration -> Roles -> New Role) with the permissions a "
                "telecaller should actually have, then re-run this script."
            )
        print(f"TELECALLER role id: {telecaller_role.id}")

        if dry_run:
            print("\n--dry-run: no changes made, telecaller account not created.")
            return

        try:
            new_user = await user_service.create_user(
                name=telecaller_name,
                email=telecaller_email,
                phone=None,
                password=telecaller_password,
                role_ids=[telecaller_role.id],
            )
        except ConflictError as exc:
            print(f"Could not create telecaller account: {exc.message}")
            return
        print(f"Created user id={new_user.id} email={telecaller_email} role=TELECALLER")
        print(
            "\nLog the admin account out and back in (or clear its stored token) so "
            "the corrected roles take effect -- the frontend caches roles from the "
            "current session/token, not just the database."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--telecaller-email", required=True)
    parser.add_argument("--telecaller-password", required=True)
    parser.add_argument("--telecaller-name", default="Telecaller")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(
            run_with_cleanup(
                _run(
                    admin_email=args.admin_email,
                    telecaller_email=args.telecaller_email,
                    telecaller_password=args.telecaller_password,
                    telecaller_name=args.telecaller_name,
                    dry_run=args.dry_run,
                )
            )
        )
    except NotFoundError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
