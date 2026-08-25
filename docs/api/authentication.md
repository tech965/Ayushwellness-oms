# Authentication

Status: **IMPLEMENTED** (Phase 1). `app/core/security.py` (token
utilities), the `/auth/*` endpoints
(`app/api/v1/endpoints/auth.py`), and the `User`/`RefreshToken` models
(`app/models/auth.py`) are all live.

## Design

- **Password storage**: Argon2 (`passlib[argon2]`) — never plaintext,
  never a fast general-purpose hash.
- **Access token**: JWT, HS256, signed with `JWT_SECRET`, short-lived
  (`JWT_ACCESS_TOKEN_EXPIRE`, default 900s / 15 min). Sent as
  `Authorization: Bearer <token>`.
- **Refresh token**: JWT with a `jti` claim, long-lived
  (`JWT_REFRESH_TOKEN_EXPIRE`, default 14 days), and **persisted** in the
  `RefreshToken` table so it can be revoked server-side (logout, admin
  action) — a stolen refresh token is not valid forever just because it
  hasn't expired.

## Endpoints (`/api/v1/auth`)

| Method | Path | Auth required | Purpose |
|---|---|---|---|
| POST | `/auth/login` | no | email + password → access + refresh token |
| POST | `/auth/refresh` | no (refresh token in body) | refresh token → new access token |
| POST | `/auth/logout` | yes | revokes the refresh token (`jti`) |
| GET | `/auth/me` | yes | current user, roles, and effective permissions |

## Frontend integration

`apps/web/lib/api-client.ts` stores both tokens in `localStorage`
(`oms_access_token`, `oms_refresh_token`) and attaches the access token
to every request. On a `401`, its response interceptor attempts one
`POST /auth/refresh` (concurrent 401s are coalesced into a single
refresh call) and replays the original request; if refresh also fails,
it clears both tokens and hard-navigates to `/login`.

`apps/web/lib/auth-context.tsx`'s `AuthProvider` wraps the
`(dashboard)` route group: on mount it calls `GET /auth/me` and
redirects to `/login` if there's no token or the call fails. There is
deliberately **no Next.js middleware** — tokens live in `localStorage`,
which Edge middleware can't read, so route protection is this
client-side guard instead. `apps/web/app/login/page.tsx` redirects to
`/dashboard` if a token is already present.

## Authorization (RBAC)

JWT authentication answers "who is this?"; RBAC (see
`docs/security/rbac.md`) answers "what can they do?". A valid token is
necessary but not sufficient — every mutating endpoint additionally
checks a permission via `Depends(require_permission("orders.update"))`
(`app/dependencies/auth.py`).
