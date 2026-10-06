"""Authenticated, same-origin Web Push subscription management."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .push_events import DEFAULT_PREFERENCES
from .push_service import PushRateLimit, PushService
from .push_store import SubscriptionConflict


def _origin_authority(value: str) -> tuple[str, int]:
    try:
        if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value:
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any((parsed.path, parsed.query, parsed.fragment))
        ):
            raise ValueError
        return parsed.hostname, parsed.port or 443
    except ValueError:
        raise HTTPException(status_code=403, detail="Same-origin HTTPS request required") from None


def verify_origin(request: Request) -> None:
    """Use browser Origin and preserved Host, never untrusted forwarded authority.

    A TLS ingress legitimately talks HTTP to this process. HTTPS Origin must
    match its preserved Host exactly; forwarded host/proto headers cannot grant
    access. Browsers cannot forge Origin or send cross-origin JSON without CORS.
    """
    origin = _origin_authority(request.headers.get("origin", ""))
    host = _origin_authority("https://" + request.headers.get("host", ""))
    if origin != host or request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(status_code=403, detail="Same-origin HTTPS request required")
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(status_code=415, detail="JSON request required")


async def request_body(request: Request) -> dict:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            count = int(length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid request body") from None
        if count < 0 or count > 8192:
            raise HTTPException(status_code=413, detail="Request body too large")
    data = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 8192:
                    raise HTTPException(status_code=413, detail="Request body too large")
        body = json.loads(data)
    except (ValueError, UnicodeDecodeError, TimeoutError):
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    return body


def _response(value: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


def install_push_api(
    app: FastAPI, get_service: Callable[[], PushService | None], authorize: Callable
) -> None:
    """Register narrow APIs; test factories can inject an entirely offline service."""
    router = APIRouter(prefix="/api/notifications")
    mutation_times: deque[float] = deque()

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/notifications/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def mutation(request: Request) -> tuple[PushService, dict]:
        authorize(request)
        verify_origin(request)
        now = time.monotonic()
        while mutation_times and now - mutation_times[0] >= 60:
            mutation_times.popleft()
        if len(mutation_times) >= 60:
            raise HTTPException(status_code=429, detail="Notification API rate limit reached")
        mutation_times.append(now)
        service = get_service()
        if service is None or not service.available:
            raise HTTPException(status_code=503, detail="Web Push unavailable")
        return service, await request_body(request)

    def endpoint(body: dict) -> str:
        if set(body) != {"endpoint"} or not isinstance(body["endpoint"], str):
            raise HTTPException(status_code=400, detail="Invalid subscription request")
        return body["endpoint"]

    def guarded(operation: Callable):
        try:
            return operation()
        except SubscriptionConflict:
            raise HTTPException(status_code=409, detail="Subscription keys conflict") from None
        except PushRateLimit:
            raise HTTPException(status_code=429, detail="Notification rate limit reached") from None
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid subscription request") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="Subscription not registered") from None
        except (sqlite3.Error, OSError):
            service = get_service()
            if service is not None:
                service.fail()
            raise HTTPException(status_code=503, detail="Web Push unavailable") from None

    @router.get("/status")
    async def status(request: Request):
        authorize(request)
        service = get_service()
        if service is not None:
            return _response(service.status())
        return _response(
            {
                "enabled": False,
                "available": False,
                "reason": "disabled",
                "publicKey": None,
                "preferencesDefaults": dict(DEFAULT_PREFERENCES),
                "maxNotificationAgeSeconds": 300,
            }
        )

    @router.post("/subscription/status")
    async def subscription_status(request: Request):
        service, body = await mutation(request)
        registered = guarded(lambda: service.registered(endpoint(body)))
        return _response(
            {
                "registered": registered is not None,
                "preferences": registered["preferences"]
                if registered
                else dict(DEFAULT_PREFERENCES),
            }
        )

    @router.post("/subscription")
    async def subscribe(request: Request):
        service, body = await mutation(request)
        if set(body) != {"subscription", "preferences"}:
            raise HTTPException(status_code=400, detail="Invalid subscription request")
        selected = guarded(lambda: service.register(body["subscription"], body["preferences"]))
        return _response({"registered": True, "preferences": selected})

    @router.delete("/subscription")
    async def unsubscribe(request: Request):
        service, body = await mutation(request)
        guarded(lambda: service.delete(endpoint(body)))
        return _response({"registered": False})

    @router.post("/test")
    async def test(request: Request):
        service, body = await mutation(request)
        guarded(lambda: service.test(endpoint(body)))
        return _response({"queued": True}, 202)

    app.include_router(router)
