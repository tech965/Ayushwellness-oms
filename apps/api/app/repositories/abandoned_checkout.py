from __future__ import annotations

from app.models.abandoned_checkout import AbandonedCheckout
from app.repositories.base import BaseRepository


class AbandonedCheckoutRepository(BaseRepository[AbandonedCheckout]):
    model = AbandonedCheckout
