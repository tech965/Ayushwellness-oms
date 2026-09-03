"""GraphQL Admin API query documents.

Authored against the documented 2026-01 GraphQL Admin API schema shape.
Shopify's schema evolves quarterly — re-verify field/enum names against
a live shop's introspection (`shopify.dev/docs/api/admin-graphql`)
before pointing this at a real store for the first time; the normalizer
(`app.integrations.shopify.normalizer`) reads every field defensively
via `.get()` so a renamed/missing field degrades that one value to
`None` instead of crashing the sync.
"""

from __future__ import annotations

SHOP_PING_QUERY = """
query ShopPing {
  shop {
    name
    myshopifyDomain
  }
}
"""

CUSTOMERS_QUERY = """
query Customers($first: Int!, $after: String, $query: String) {
  customers(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        firstName
        lastName
        email
        phone
        state
        createdAt
        updatedAt
        defaultAddress {
          id
          name
          address1
          address2
          city
          province
          country
          zip
          phone
        }
        addresses {
          id
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
    }
  }
}
"""

PRODUCTS_QUERY = """
query Products($first: Int!, $after: String, $query: String) {
  products(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        title
        descriptionHtml
        vendor
        productType
        status
        tags
        createdAt
        updatedAt
        variants(first: 100) {
          edges {
            node {
              id
              sku
              title
              price
              compareAtPrice
              inventoryQuantity
              barcode
              selectedOptions { name value }
            }
          }
        }
      }
    }
  }
}
"""

ORDERS_QUERY = """
query Orders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        name
        createdAt
        updatedAt
        cancelledAt
        currencyCode
        displayFinancialStatus
        displayFulfillmentStatus
        tags
        note
        subtotalPriceSet { shopMoney { amount } }
        totalDiscountsSet { shopMoney { amount } }
        totalTaxSet { shopMoney { amount } }
        totalPriceSet { shopMoney { amount } }
        shippingLine { originalPriceSet { shopMoney { amount } } }
        paymentGatewayNames
        customer { id }
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
        billingAddress {
          name
          address1
          address2
          city
          province
          country
          zip
          phone
        }
        lineItems(first: 100) {
          edges {
            node {
              id
              sku
              title
              quantity
              originalUnitPriceSet { shopMoney { amount } }
              discountedTotalSet { shopMoney { amount } }
              variant { id }
            }
          }
        }
        # Plain list (not a connection — no edges/node), confirmed against
        # the live schema. `displayStatus` is the actual delivery-progress
        # status (IN_TRANSIT/OUT_FOR_DELIVERY/DELIVERED/... — 18 values),
        # distinct from `displayFulfillmentStatus` above (only
        # UNFULFILLED/PARTIALLY_FULFILLED/FULFILLED/...). An order can have
        # more than one fulfillment (split shipments); the normalizer takes
        # the last one as "current", matching `_to_list_response`'s existing
        # `order.shipments[-1]` convention for Shiprocket shipments.
        fulfillments(first: 10) {
          displayStatus
        }
      }
    }
  }
}
"""

ENTITY_QUERIES: dict[str, str] = {
    "customers": CUSTOMERS_QUERY,
    "products": PRODUCTS_QUERY,
    "orders": ORDERS_QUERY,
}


def updated_since_filter(since_iso: str) -> str:
    """Shopify search-syntax filter for the `query` argument — the
    mechanism the GraphQL Admin API uses for incremental sync (no
    separate "changes since" endpoint exists).
    """
    return f"updated_at:>'{since_iso}'"
