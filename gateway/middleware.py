"""API key authentication middleware."""

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse


async def check_api_key(request: Request, call_next):
    """Require Bearer token on /api/* routes. Health and approval pages are open."""
    from gateway.app import get_api_key

    api_key = get_api_key()
    if api_key and request.url.path.startswith("/api/"):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
    return await call_next(request)
