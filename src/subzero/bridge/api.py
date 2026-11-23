"""HTTP routes for the terminal-to-browser session bridge."""

from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from subzero.config import Settings
from subzero.ingest import _bearer_token, _validate_device_token
from subzero.models import BridgeMessageCreate, BridgeSessionCreate
from subzero.services import get_services


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _sender_authorized(request: Request, settings: Settings) -> bool:
    supplied = _bearer_token(request)
    expected = settings.harness_api_key.get_secret_value()
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied, expected)


async def _json(request: Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def build_bridge_routes(settings: Settings) -> list[Route]:
    async def create_session(request: Request) -> JSONResponse:
        if not _sender_authorized(request, settings):
            return _unauthorized()
        body = await _json(request)
        if body is None:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        try:
            request_data = BridgeSessionCreate.model_validate(body)
        except ValidationError:
            return JSONResponse({"error": "invalid session fields"}, status_code=400)
        if not request_data.provider.strip():
            return JSONResponse({"error": "provider must be a non-empty string"}, status_code=400)
        result = await get_services().bridge.create_session(
            provider=request_data.provider.strip(),
            title=request_data.title,
            model=request_data.model,
        )
        return JSONResponse(result.model_dump(mode="json"))

    async def get_session(request: Request) -> JSONResponse:
        token = _bearer_token(request)
        if not token or await _validate_device_token(get_services().db, token) is None:
            return _unauthorized()
        try:
            session_id = UUID(request.path_params["session_id"])
        except (KeyError, ValueError):
            return JSONResponse({"error": "not found"}, status_code=404)
        result = await get_services().bridge.get_session(session_id)
        if result is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(result.model_dump(mode="json"))

    async def append_message(request: Request) -> JSONResponse:
        if not _sender_authorized(request, settings):
            return _unauthorized()
        try:
            session_id = UUID(request.path_params["session_id"])
        except (KeyError, ValueError):
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json(request)
        if body is None:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        if "text" not in body and "content" in body:
            body["text"] = body["content"]
        try:
            request_data = BridgeMessageCreate.model_validate(body)
        except ValidationError:
            return JSONResponse({"error": "invalid message fields"}, status_code=400)
        if not request_data.text.strip():
            return JSONResponse({"error": "text is required"}, status_code=400)
        result = await get_services().bridge.append_message(
            session_id,
            content=request_data.text,
            role=request_data.role,
            model=request_data.model,
        )
        if result is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(result.model_dump(mode="json"))

    return [
        Route("/api/v1/sessions", create_session, methods=["POST"]),
        Route("/api/v1/sessions/{session_id}", get_session, methods=["GET"]),
        Route("/api/v1/sessions/{session_id}/messages", append_message, methods=["POST"]),
    ]
