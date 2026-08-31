# Cashfree Payments Integration

Status: **IMPLEMENTED**, tested entirely against **mocked** Cashfree
responses (`httpx.MockTransport` for the client, hand-signed payloads
for the webhook). **No live Cashfree account was available** — nothing
here has been exercised against a real sandbox or production delivery.
See "Testing procedure" and "Failure troubleshooting" below for exactly
what to do before trusting this in production.

## 1. What already existed

- A `Payment`/`PaymentTransaction` schema already scaffolded for "a
  payment provider starting Phase 2+", entirely unused until now (zero
  `PaymentTransaction` rows were ever created anywhere in the codebase).
- Generic `WebhookEvent`/`WebhookService` idempotency infrastructure,
  already proven out by the Shopify and Shiprocket webhooks.
- `Order.payment_status`/`Order.payment_type`/`Order.total_amount` are
  already the sole inputs to every dashboard/revenue/COD-vs-prepaid
  calculation in `app.services.analytics_service` — **no analytics code
  was changed**; a Cashfree payment marking `Order.payment_status = PAID`
  is automatically reflected everywhere revenue/payment breakdowns are
  already computed.
- `IntegrationCode`/`SourceSystem`/`scripts/seed.py`'s idempotent
  `Integration` row seeding, extended with a `"cashfree"` entry the same
  way Shopify/Shiprocket already work.

## 2. Sandbox setup

1. Create a Cashfree test/sandbox account at https://merchant.cashfree.com.
2. Settings → API Keys → generate a sandbox **Client ID** and **Client
   Secret**.
3. Set locally (`.env`, never committed):
   ```
   CASHFREE_CLIENT_ID=<sandbox client id>
   CASHFREE_CLIENT_SECRET=<sandbox client secret>
   CASHFREE_API_URL=https://sandbox.cashfree.com/pg   # default — safe to omit
   ```
4. Local development works with no credentials at all — every payment
   check reports "not configured" rather than failing the API or
   attempting a network call, matching Shopify/Shiprocket's existing
   convention (`app.integrations.cashfree.config.CashfreeConfig
   .from_settings()` returns `None`).

## 3. Production setup

1. In Cashfree's live dashboard, generate **production** Client ID/Secret.
2. In Render (see §17): set `CASHFREE_API_URL=https://api.cashfree.com/pg`
   and the production `CASHFREE_CLIENT_ID`/`CASHFREE_CLIENT_SECRET`.
3. Configure the production webhook (see §6).
4. **Do not** switch `CASHFREE_API_URL` to production until §9's live
   verification steps have been completed against sandbox first.

## 4. Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `CASHFREE_CLIENT_ID` | For any Cashfree call | `None` (unconfigured) | Payments API auth (`x-client-id`) |
| `CASHFREE_CLIENT_SECRET` | For any Cashfree call | `None` (unconfigured) | Payments API auth (`x-client-secret`) **and** the webhook signing key (see §8) |
| `CASHFREE_API_VERSION` | No | `2025-01-01` | `x-api-version` header on every request |
| `CASHFREE_API_URL` | No | `https://sandbox.cashfree.com/pg` (sandbox-safe default) | `https://api.cashfree.com/pg` for production |
| `CASHFREE_WEBHOOK_SECRET` | No | unset → falls back to `CASHFREE_CLIENT_SECRET` | Only needed if this specific Cashfree account has a webhook signing key distinct from its client secret |
| `CASHFREE_RETURN_URL` | No | unset (checkout works without it) | `order_meta.return_url` template; a `{order_id}` placeholder is substituted with the OMS order id |

Never logged: `CASHFREE_CLIENT_SECRET`, `CASHFREE_WEBHOOK_SECRET`, or any
resolved webhook secret value — confirmed by
`test_get_payment_status_returns_safe_fields_only` and the structured
logging throughout `app.api.v1.webhooks.cashfree`/
`app.integrations.cashfree.webhooks`.

## 5. API version / base URLs

Cashfree Payments API **2025-01-01**.

- Sandbox: `https://sandbox.cashfree.com/pg`
- Production: `https://api.cashfree.com/pg`

## 6. Webhook URL

    POST https://<your-render-domain>/api/v1/webhooks/cashfree/payment

Configure this in Cashfree's dashboard under the Payment Gateway
webhook settings (webhook events section, not "SR Checkout" or any
other Cashfree product — this project uses the core Payments API, not
Cashfree Checkout-as-a-product).

## 7. Cashfree dashboard configuration

1. Merchant Dashboard → Developers → Webhooks → Payments.
2. Add the URL from §6.
3. Subscribe to: `PAYMENT_SUCCESS_WEBHOOK`, `PAYMENT_FAILED_WEBHOOK`,
   `PAYMENT_USER_DROPPED_WEBHOOK`.
4. Cashfree signs every webhook with the Payments API **Client Secret**
   by default (confirmed across Cashfree's own documentation and SDK
   examples — see §8); there is normally nothing further to configure
   here unless this specific account has a distinct webhook signing key,
   in which case set `CASHFREE_WEBHOOK_SECRET` to it.

## 8. Webhook events

Recognized event `type` values
(`app.integrations.cashfree.normalizer.RECOGNIZED_WEBHOOK_TYPES`):

- `PAYMENT_SUCCESS_WEBHOOK`
- `PAYMENT_FAILED_WEBHOOK`
- `PAYMENT_USER_DROPPED_WEBHOOK`

Any other `type` is safely ignored — the `WebhookEvent` is still
recorded (`status=ignored`, reason `unrecognized_event_type:<type>`),
never treated as a payment outcome.

Payload shape (confirmed via Cashfree's official API reference/webhook
documentation):

```json
{
  "type": "PAYMENT_SUCCESS_WEBHOOK",
  "event_time": "2026-02-01T10:00:05+05:30",
  "data": {
    "order": {"order_id": "AWL92268", "order_amount": 500.00, "order_currency": "INR"},
    "payment": {
      "cf_payment_id": "...", "payment_status": "SUCCESS", "payment_amount": 500.00,
      "payment_currency": "INR", "payment_time": "...", "payment_method": {"upi": {}}
    },
    "customer_details": {"customer_id": "...", "customer_phone": "..."},
    "payment_gateway_details": {"gateway_name": "CASHFREE", "...": "..."},
    "error_details": {"error_code": "...", "...": "..."}
  }
}
```

The actual event outcome is always read from `data.payment.payment_status`
(`SUCCESS`/`FAILED`/`USER_DROPPED`/...), never assumed from the
top-level `type` alone — see
`app.services.cashfree_payment_service.CashfreePaymentService
.apply_payment_event`.

## 9. Signature verification

Confirmed via Cashfree's official webhook security documentation:

```
signed_payload = x-webhook-timestamp header (raw string) + RAW REQUEST BODY (raw bytes)
signature      = Base64Encode(HMAC-SHA256(signed_payload, secret))
secret         = CASHFREE_WEBHOOK_SECRET if set, else CASHFREE_CLIENT_SECRET
```

Compared against the `x-webhook-signature` header with a constant-time
comparison (`hmac.compare_digest`). Implemented in
`app.integrations.cashfree.webhooks.verify_webhook_signature`.

**Critical, and enforced in code**: `app.api.v1.webhooks.cashfree
.receive_cashfree_payment_webhook` reads `await request.body()` and
verifies the signature against those exact raw bytes *before* any JSON
parsing. A missing signature, missing timestamp, or a mismatch (e.g. a
tampered body) is rejected with `401` and never reaches JSON parsing —
so a request that fails signature verification can never leak whether
its JSON was even well-formed.

## 10. Idempotency

Two layers, matching the Shopify/Shiprocket pattern:

1. **`WebhookEvent`** (`app.services.webhook_service.WebhookService
   .ingest`): `external_event_id = data.payment.cf_payment_id` when
   present (Cashfree's own documented idempotency guidance: "track
   `cf_payment_id`... process it only once, regardless of retries").
   Falls back to the generic `compute_fallback_event_id` (a deterministic
   hash of integration + event_type + payload) when absent. A duplicate
   delivery is acked `200` with zero further processing.
2. **`PaymentTransaction`** (`(gateway, gateway_transaction_id)` unique
   constraint, `PaymentTransactionRepository.create_if_new`): a
   defense-in-depth backstop, primarily load-bearing for the
   reconciliation path (§13), which doesn't go through `WebhookEvent` at
   all and could otherwise be re-run.

## 11. Payment state machine

`Payment.status`/`Order.payment_status` reuse the **existing**
`PaymentStatus` enum (`pending`/`paid`/`failed`/...) — no new status
system was introduced. Cashfree's finer-grained states map onto it:

| Cashfree `payment_status` | OMS `PaymentStatus` |
|---|---|
| `SUCCESS` | `PAID` |
| `FAILED`, `USER_DROPPED`, `CANCELLED`, `VOID` | `FAILED` |
| `PENDING`, `NOT_ATTEMPTED`, `FLAGGED` | `PENDING` |
| anything else | *(unmapped — never guessed; event recorded as `ignored`)* |

Rules enforced in `CashfreePaymentService.apply_payment_event`:

- **Never trusts the frontend.** The webhook (signature-verified) and
  the reconciliation API call (server-to-server, authenticated) are the
  only two callers.
- **`PAID` is terminal/sticky** — a later non-`PAID` event for the same
  `Payment` (a stale retry, an out-of-order delivery) is recorded as a
  `PaymentTransaction` for the audit trail but never downgrades the
  payment or the order.
- **`FAILED` → `PAID` is allowed** when driven by a new, genuinely
  successful payment attempt (a different `cf_payment_id` — the customer
  retried with a different instrument). This is exactly the "trusted
  Cashfree event... proves it" case the spec allows.
- A `FAILED`/`USER_DROPPED` event never changes `Order.payment_status`
  away from `PENDING` — the customer can still retry via a fresh
  checkout session.
- On `PAID`: `Order.payment_status → PAID`, and if `Order.status` is
  still `PENDING`, it transitions to `CONFIRMED` (the existing
  `ORDER_STATUS_TRANSITIONS` machine in `app.services.order_service` —
  not a new one). An `OrderEvent` is written either way (paid or
  failed) for the order timeline.

## 12. Amount validation

`apply_payment_event` compares `data.payment.payment_amount`/
`payment_currency` against the `Payment.amount`/`currency` recorded at
checkout-creation time (itself always the server-side `Order
.total_amount`, never anything the browser could have supplied). A
mismatch on a `SUCCESS` event:

- does **not** mark the payment/order paid,
- is recorded as an `ignored` `WebhookEvent` with reason
  `amount_mismatch:expected=<X>:received=<Y>` for reconciliation,
- still acks `200` (so Cashfree doesn't retry something a retry can't
  fix).

All monetary comparison uses `decimal.Decimal` end to end. The webhook
body is parsed **twice**: once normally (floats, matching every other
webhook payload this codebase stores) for `WebhookEvent.payload`, and a
second time with `json.loads(raw_body, parse_float=Decimal)` used *only*
to extract the payment amount with zero floating-point intermediation —
see `app.integrations.cashfree.normalizer.extract_decimal_amount` and
the webhook endpoint's docstring.

## 13. Reconciliation

`POST /api/v1/payments/cashfree/orders/{order_id}/reconcile`
(`payments.create` permission) — an authenticated, on-demand fallback
for a delayed/missed webhook. Calls `GET /orders/{order_id}/payments`
and applies every payment attempt Cashfree currently reports through
the **exact same** `apply_payment_event` the webhook uses (no separate
logic to drift out of sync).

Deliberately **not** a scheduled/polled Celery task — the spec asked for
a fallback, not aggressive polling, and this project's existing
Shopify/Shiprocket reconciliation infrastructure
(`app.services.reconciliation_service`) is a periodic *read-only*
diff/report tool, a different shape than "apply Cashfree's authoritative
state." If delayed-webhook incidents turn out to be common in practice,
wiring a scheduled version of `CashfreePaymentService.reconcile_payment`
into `app.tasks` (mirroring `app.tasks.shiprocket_sync`) is the natural
next step — not implemented here to avoid speculative complexity.

## 14. Testing procedure (do this before trusting sandbox)

1. Set sandbox credentials locally (§2).
2. `POST /api/v1/payments/cashfree/orders/{order_id}/create` (staff
   token with `payments.create`) for a real OMS order with a customer
   phone on file. Confirm a `payment_session_id` comes back.
3. Open the order in the OMS dashboard (`/orders/{id}`) — the "Cashfree
   Payment" card's "Collect Payment via Cashfree" / "Resume Checkout"
   button loads Cashfree's official Checkout JS SDK
   (`https://sdk.cashfree.com/js/v3/cashfree.js`) and opens checkout
   with that `payment_session_id`.
4. Complete a **sandbox** test payment using Cashfree's documented test
   instruments.
5. Confirm the webhook actually reaches the deployed endpoint (§6) —
   check `GET /api/v1/webhook-events?integration_id=<cashfree>` for a
   new row with `status=processed`.
6. Confirm on the order: `payment_status=paid`, `status=confirmed`, and
   the amount/timestamp are correct.
7. Repeat with a card/instrument that fails, and one that's abandoned
   mid-checkout — confirm `Payment.status=failed` and
   `Order.payment_status` stays `pending` (never falsely marked paid).
8. Only then repeat against production credentials.

## 15. Failure troubleshooting

- **Webhook returns 401**: signature mismatch. Confirm
  `CASHFREE_CLIENT_SECRET` (or `CASHFREE_WEBHOOK_SECRET` if set) in
  Render exactly matches the value shown in Cashfree's dashboard — a
  copy-paste trailing newline/space is a common cause (Shopify's webhook
  hit this exact issue; this endpoint hashes the secret as configured,
  with no implicit `.strip()`, so paste carefully).
- **Webhook returns 404**: the `cashfree` `Integration` row doesn't
  exist yet — run `scripts/seed.py` against the target database.
- **Webhook returns 200 but the order never updates**: check
  `GET /api/v1/webhook-events` for that delivery's `status`. `ignored`
  with reason `unknown_cashfree_order` means the `order_id` Cashfree
  webhooked back doesn't match any `Payment.external_id` the OMS
  created — almost always means checkout was created through a path
  other than `POST .../create` (not expected in this integration).
  `amount_mismatch`/`currency_mismatch` means exactly what it says —
  investigate before ever manually marking the order paid.
- **Checkout creation fails with "no customer phone number on file"**:
  Cashfree requires `customer_details.customer_phone`; the order's
  shipping address / linked customer has none. Add one before retrying.

## Known limitations / not yet verified live

- No live Cashfree account exists in this environment — every field
  name above is sourced from Cashfree's official documentation/SDK
  references (URLs recorded in the implementation PR), never invented,
  but **none of it has been exercised against a real delivery**.
- `IntegrationType` has no dedicated "payment gateway" value; the seeded
  Cashfree `Integration` row uses `ECOMMERCE` (nothing in the app
  branches on this field — confirmed by inspection) rather than adding
  one via an Alembic `ALTER TYPE ... ADD VALUE` migration for a purely
  descriptive column.
- Reconciliation is on-demand only (§13) — no scheduled task.

**Do not claim this integration is fully working in production until a
real Cashfree payment has been completed and a real Cashfree webhook has
been received and processed by the deployed OMS** (§14, step 8).
