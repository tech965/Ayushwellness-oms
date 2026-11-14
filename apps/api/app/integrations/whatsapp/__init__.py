"""WhatsApp Cloud API integration -- outbound sends + inbound webhook.

Not registered into `app.integrations.registry`/`SyncService` (that
machinery is for pulling marketplace order data; this is OMS-initiated
outbound notification + delivery tracking, a different shape of
integration entirely) -- see `app.services.whatsapp_send_service` and
`app.tasks.whatsapp_tasks` for how this package is actually used.
"""

from __future__ import annotations
