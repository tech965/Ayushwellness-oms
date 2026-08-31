# Environment Variables

Copy `.env.example` (repo root) to `.env` before running Docker Compose,
and `apps/web/.env.example` to `apps/web/.env.local` before running the
frontend directly with `npm run dev`. **Never commit `.env` or
`.env.local`.**

| Variable | Used by | Phase | Notes |
|---|---|---|---|
| `ENVIRONMENT` | API | 0 | `development` \| `staging` \| `production` \| `test` |
| `DEBUG` | API | 0 | |
| `DATABASE_URL` | API | 0 | `postgresql+asyncpg://...` |
| `REDIS_URL` | API, Celery | 0 | |
| `JWT_SECRET` | API | 0 (used from) / 1 (endpoints) | Generate a long random value for anything beyond local dev |
| `JWT_ACCESS_TOKEN_EXPIRE` | API | 0/1 | seconds, default 900 |
| `JWT_REFRESH_TOKEN_EXPIRE` | API | 0/1 | seconds, default 1209600 |
| `NEXT_PUBLIC_API_URL` | Web | 0 | Public — never put secrets in a `NEXT_PUBLIC_*` var |
| `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` / `SHOPIFY_ACCESS_TOKEN` / `SHOPIFY_WEBHOOK_SECRET` / `SHOPIFY_STORE_DOMAIN` | API | 2 | Leave blank until Shopify credentials are issued — the app starts fine without them |
| `SHIPROCKET_EMAIL` / `SHIPROCKET_PASSWORD` / `SHIPROCKET_API_URL` / `SHIPROCKET_WEBHOOK_SECRET` | API | 2 | Same |
| `BLUE_DART_API_URL` / `BLUE_DART_API_KEY` / `BLUE_DART_CLIENT_ID` / `BLUE_DART_CLIENT_SECRET` | API | 2 | Same |
| `CASHFREE_CLIENT_ID` / `CASHFREE_CLIENT_SECRET` / `CASHFREE_API_VERSION` / `CASHFREE_API_URL` / `CASHFREE_WEBHOOK_SECRET` / `CASHFREE_RETURN_URL` | API | 2 | Same — see docs/integrations/cashfree.md |
| `WHATSAPP_API_URL` / `WHATSAPP_ACCESS_TOKEN` | API | 5 | Not read by any code yet |
| `META_APP_ID` / `META_APP_SECRET` / `META_ACCESS_TOKEN` | API | 6 | Not read by any code yet |

## Full list of settings

`apps/api/app/core/config.py` (`Settings`) is the source of truth — every
variable above (and a few internal-only ones like `DATABASE_POOL_SIZE`,
`RATE_LIMIT_DEFAULT`, `LOG_LEVEL`) is defined there with a sane default so
the API starts with zero configuration for local development.
