# Courier Integrations

Status: PLANNED (Phase 2 foundation). `apps/api/app/integrations/couriers/`
currently contains only a package docstring.

## Why a generic courier interface

Shiprocket already abstracts several couriers, but AyushWellness also
needs direct integrations (Blue Dart first, then Delhivery and Ecom
Express) for couriers or lanes Shiprocket doesn't cover well. Rather than
special-casing each courier in OMS services, every direct courier
integration implements the same adapter interface so `app/services/*`
never needs to know which courier it's talking to.

## Planned interface

A `CourierAdapter` protocol/ABC (introduced when the first real courier is
wired up in Phase 2) will define, at minimum:

- `create_shipment(...)`
- `get_tracking(awb)`
- `cancel_shipment(awb)`

Each courier package (`blue_dart/`, `delhivery/`, `ecom_express/` —
created as they're implemented) provides a `Client` + `Config` +
concrete `Adapter` + `Normalizer`, following the same pattern as
`docs/architecture/integrations.md`.

## Blue Dart (first direct courier — Phase 2)

Env vars: `BLUE_DART_API_URL`, `BLUE_DART_API_KEY`,
`BLUE_DART_CLIENT_ID`, `BLUE_DART_CLIENT_SECRET`. No live API calls exist
yet — this is credential plumbing only, ready for the Phase 2
implementation.

## Adding a new courier later

1. Create `app/integrations/couriers/<courier>/` with
   Client/Config/Adapter/Normalizer.
2. Register it wherever couriers are looked up by code (Phase 2 —
   likely a small registry keyed by `Courier.code` from the database).
3. Add its webhook payload shape to
   `app/api/v1/webhooks/couriers.py` dispatch logic (already routes by
   `courier_code`, no new route needed).

No change to `app/services/shipments.py`, `app/models/shipments.py`, or
any UI code is required — that's the point of the adapter pattern.
