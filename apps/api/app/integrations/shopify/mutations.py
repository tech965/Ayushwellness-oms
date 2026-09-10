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

# The exact inverse of `tagsAdd` -- removing a tag the order doesn't have
# is documented as a no-op too, so `ShopifyFulfillmentService.
# reverse_confirmation_fulfillment` can call this unconditionally on every
# unconfirm/retry with no local "was it tagged" bookkeeping, same as the
# forward push.
TAGS_REMOVE_MUTATION = """
mutation TagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
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

# Cancels one specific Fulfillment by id -- never a whole order or a
# FulfillmentOrder. `ShopifyFulfillmentService.reverse_confirmation_
# fulfillment` only ever passes the exact `Order.
# shopify_confirmation_fulfillment_id` this OMS itself recorded at
# confirm time, so this can never touch a Fulfillment created
# independently outside the OMS.
FULFILLMENT_CANCEL_MUTATION = """
mutation FulfillmentCancel($id: ID!) {
  fulfillmentCancel(id: $id) {
    fulfillment {
      id
      status
    }
    userErrors {
      field
      message
    }
  }
}
"""

# Attaches/updates tracking info on an EXISTING Fulfillment -- used only
# when Telecaller confirmation already closed the order's one
# FulfillmentOrder (no `fulfillmentCreate` left to make): real shipping
# (AWB assignment) then calls this on that same Fulfillment instead of
# trying to create a second one, so `sync_fulfillment_for_shipment` never
# silently no-ops just because OMS confirmation got there first. See that
# method's docstring.
FULFILLMENT_TRACKING_INFO_UPDATE_MUTATION = """
mutation FulfillmentTrackingInfoUpdate(
  $fulfillmentId: ID!
  $trackingInfoInput: FulfillmentTrackingInput!
  $notifyCustomer: Boolean
) {
  fulfillmentTrackingInfoUpdate(
    fulfillmentId: $fulfillmentId
    trackingInfoInput: $trackingInfoInput
    notifyCustomer: $notifyCustomer
  ) {
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

# Updates the shipping address already on an existing Shopify order --
# `orderUpdate`'s `shippingAddress` sets the order's `MailingAddress` in
# place (no new object, no id to track), so re-sending the OMS's current
# `Order.shipping_address` is always safe/idempotent; there is no
# "already synced" guard the way the Fulfillment pushes need one. Used
# only for a Telecaller-initiated address correction
# (`ShopifyFulfillmentService.sync_shipping_address`) -- never touches
# `lineItems`/financial fields/tags, so it can't be confused with any
# other outbound push in this file.
ORDER_UPDATE_SHIPPING_ADDRESS_MUTATION = """
mutation OrderUpdateShippingAddress($input: OrderInput!) {
  orderUpdate(input: $input) {
    order {
      id
      shippingAddress {
        name
        address1
        address2
        city
        province
        country
        zip
        phone
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""
