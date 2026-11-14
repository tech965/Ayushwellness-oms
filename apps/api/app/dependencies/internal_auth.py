"""Service-to-service auth for internal endpoints -- distinct from
`app.dependencies.auth`'s user/JWT model, since the caller here is
another backend (Sales CRM), not a logged-in OMS user.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.core.config import settings


def require_sales_crm_forward_secret(authorization: str | None = Header(default=None)) -> None:
    """Checks `Authorization: Bearer <SALES_CRM_FORWARD_SECRET>` with a
    constant-time comparison (same `hmac.compare_digest` approach as
    `app.integrations.whatsapp.webhook_security.verify_signature`).
    Missing config, missing header, or any mismatch -> 401 -- never
    treats "can't verify" as "verified".
    """
    secret = settings.SALES_CRM_FORWARD_SECRET
    if not secret:
        raise HTTPException(status_code=401, detail="Not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer ") :]
    if not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="Invalid token")
