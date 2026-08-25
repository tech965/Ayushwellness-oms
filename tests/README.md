# tests/

Intentionally empty. Backend tests live in `apps/api/tests/` (pytest) and
frontend tests live next to the code they test in `apps/web/**/__tests__/`
(Vitest) — each app owns its own test suite rather than sharing a
top-level runner. See `docs/development/setup.md#testing`.
