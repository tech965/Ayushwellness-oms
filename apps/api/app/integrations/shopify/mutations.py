"""GraphQL Admin API documents for the OUTBOUND OMS -> Shopify fulfillment
push (Phase 6) — the one direction `queries.py` deliberately doesn't
cover (that file is pull-sync only). Authored against the same
documented 2026-01 GraphQL Admin API schema shape as `queries.py`;
re-verify against a live shop's introspection before pointing this at a
real store for the first time.

Requires the `write_fulfillments` (and `read_fulfillments`, granted by
the same scope) Admin API access scope on the configured Shopify
app/token — a 403/`authorization_error` from `create_fulfillment` most
likely means that scope isn't granted.
"""

from __future__ import annotations

# Read-only, but lives here (not queries.py) because it exists solely to
# support the outbound fulfillment flow below -- finding which of an
# order's FulfillmentOrders are still OPEN (unfulfilled) and their id,
# which `fulfillmentCreate` needs. `status: OPEN` server-side filter
# means the result already excludes anything already fulfilled/closed/
# cancelled on Shopify's side.
OPEN_FULFILLMENT_ORDERS_QUERY = """
query OpenFulfillmentOrders($orderId: ID!) {
  order(id: $orderId) {
    id
    fulfillmentOrders(first: 10, query: "status:open") {
      edges {
        node {
          id
          status
        }
      }
    }
  }
}
"""

# Deliberately omits `lineItemsByFulfillmentOrder[].fulfillmentOrderLineItems`
# -- omitting it fulfills the FulfillmentOrder's entire remaining quantity,
# which matches this OMS's single-shipment-per-order model (no partial-
# fulfillment UI exists anywhere in this codebase). `trackingInfo` is
# optional at the Shopify API level; callers only include it once a real
# AWB/courier exists.
FULFILLMENT_CREATE_MUTATION = """
mutation FulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment {
      id
      status
      trackingInfo {
        number
        company
        url
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

# `tagsAdd` is a set-union on Shopify's side -- adding a tag the order
# already has is a documented no-op, never a duplicate. That's what makes
# `ShopifyFulfillmentService.sync_confirmation_tag` safe to call on every
# confirm/retry with no local "already tagged" bookkeeping of its own.
TAGS_ADD_MUTATION = """
mutation TagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node {
      id
    }
    userErrors {
      field
      message
    }
  }
}
"""
