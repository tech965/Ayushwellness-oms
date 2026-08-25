"""External integration adapters.

Status: PLANNED — Phase 2 onward.

Each subpackage (shopify/, shiprocket/, couriers/) implements a
client + config + adapter + webhook handler + sync service + normalizer,
so the OMS core never depends directly on an external API shape. See
docs/architecture/integrations.md.
"""
