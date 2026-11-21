"""Provider-agnostic courier operation architecture shell (2026-11-20).

Contains `CourierProvider` (the common interface), `ShiprocketProvider`
(delegates to the existing, unmodified `ShiprocketOperationsService`),
`NimbusPostProvider` (raises a schema-unverified error for every
operation — see its module docstring), and `get_courier_provider` (a
`source_system` -> provider resolver).

Not yet wired into any existing fulfillment call site — see
`app.services.courier.registry`'s module docstring.
"""

from __future__ import annotations
