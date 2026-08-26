import os
import secrets

from fastapi import Header, HTTPException, status


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    """Require a key from the comma-separated API_KEY environment variable."""
    configured_keys = [key.strip() for key in os.getenv("API_KEY", "").split(",") if key.strip()]
    if not x_api_key or not any(secrets.compare_digest(x_api_key, key) for key in configured_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
    return x_api_key
