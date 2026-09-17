import os
import secrets

from fastapi import Header, HTTPException, Request, status
from core.identity import auth_mode
from core.runtime import runtime_environment


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key"),
                          authorization: str | None = Header(default=None)) -> str:
    """Legacy dependency name; production requires verified merchant identity."""
    if runtime_environment() == "production" and request.url.path.startswith("/dev/"):
        raise HTTPException(404, "Development routes are unavailable.")
    if auth_mode() == "supabase":
        from api.identity import authenticate_request
        return await authenticate_request(request, authorization)
    configured_keys = [key.strip() for key in os.getenv("API_KEY", "").split(",") if key.strip()]
    if not x_api_key or not any(secrets.compare_digest(x_api_key, key) for key in configured_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
    return x_api_key
