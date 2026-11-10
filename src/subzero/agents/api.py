"""HTTP routes for coding-agent hooks, the subzero CLI and the board.

Same device tokens as the browser extension (``subzero pair-device``, then
``subzero connect``). Every route answers 401 to a missing or revoked token
before doing anything else, so an unauthenticated caller learns nothing.

    POST /api/v1/agents/start                 register or revive a session
    POST /api/v1/agents/{id}/beat             heartbeat; returns new messages
    POST /api/v1/agents/{id}/end              session finished
    GET  /api/v1/agents/board?after=<event>   everything the board draws
    POST /api/v1/messages                     send to me, a home or a session
    GET  /api/v1/messages/me                  unread messages for the person
    POST /api/v1/dispatch                     queue a job
    POST /api/v1/dispatch/claim               take the oldest job for a target
    POST /api/v1/dispatch/{id}/finish         done, failed or cancelled
    GET  /api/v1/bootstrap?q=&project=        context package for a prompt
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from subzero.errors import NotFoundError, ValidationError
from subzero.ingest import _bearer_token, _validate_device_token
from subzero.services import get_services

Handler = Callable[[Request, dict[str, Any]], Awaitable[Any]]


def _authed(handler: Handler) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def endpoint(request: Request) -> JSONResponse:
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        device = await _validate_device_token(get_services().db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            return JSONResponse(await handler(request, device))
        except ValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except NotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    return endpoint


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise ValidationError("Send a JSON object.") from exc
    if not isinstance(body, dict):
        raise ValidationError("Send a JSON object.")
    return body


def _uuid(value: Any, name: str = "id") -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an id.") from exc


def _str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be text.")
    return value


async def start(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await get_services().agents.start(
        harness=_str(body, "harness") or "unknown",
        external_id=_str(body, "external_id"),
        name=_str(body, "name"),
        machine=_str(body, "machine") or device.get("device_name"),
        cwd=_str(body, "cwd"),
        branch=_str(body, "branch"),
        home=_str(body, "home"),
        task=_str(body, "task"),
    )


async def beat(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await get_services().agents.beat(
        _uuid(request.path_params["session_id"]),
        status=_str(body, "status"),
        task=_str(body, "task"),
        home=_str(body, "home"),
    )


async def end(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await get_services().agents.end(
        _uuid(request.path_params["session_id"]), summary=_str(body, "summary")
    )


async def board(request: Request, device: dict[str, Any]) -> Any:
    try:
        after = int(request.query_params.get("after", "0"))
    except ValueError as exc:
        raise ValidationError("after must be a number.") from exc
    return await get_services().agents.board(after_event=max(0, after))


async def send(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    sender = _str(body, "from_session")
    agents = get_services().agents
    label = "me"
    if sender:
        label = (await agents.resolve_session(sender))["name"]
    return await agents.send(
        to=_str(body, "to") or "",
        body=_str(body, "body") or "",
        from_session=_uuid(sender, "from_session") if sender else None,
        from_label=label,
    )


async def my_messages(request: Request, device: dict[str, Any]) -> Any:
    mark = request.query_params.get("mark_read", "true") != "false"
    return {"messages": await get_services().agents.messages_for_me(mark_read=mark)}


async def dispatch(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await get_services().agents.dispatch(
        _str(body, "goal") or "",
        mode=_str(body, "mode") or "scout",
        target=_str(body, "target"),
        created_by=_str(body, "created_by") or str(device.get("device_name") or "cli"),
    )


async def claim(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    targets = body.get("targets") or []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise ValidationError("targets must be a list of agent names.")
    job = await get_services().agents.claim(
        _uuid(body.get("session_id"), "session_id"), targets=targets
    )
    return {"job": job}


async def finish(request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await get_services().agents.finish(
        _uuid(request.path_params["job_id"]),
        status=_str(body, "status") or "done",
        result=_str(body, "result"),
    )


async def bootstrap(request: Request, device: dict[str, Any]) -> Any:
    params = request.query_params
    try:
        budget = min(4000, max(200, int(params.get("budget", "1200"))))
    except ValueError as exc:
        raise ValidationError("budget must be a number.") from exc
    package = await get_services().retrieval.bootstrap(
        params.get("q") or None, project=params.get("project") or None, token_budget=budget
    )
    return package.model_dump(mode="json", exclude_none=True)


def build_agent_routes() -> list[Route]:
    return [
        Route("/api/v1/agents/start", _authed(start), methods=["POST"]),
        Route("/api/v1/agents/board", _authed(board), methods=["GET"]),
        Route("/api/v1/agents/{session_id}/beat", _authed(beat), methods=["POST"]),
        Route("/api/v1/agents/{session_id}/end", _authed(end), methods=["POST"]),
        Route("/api/v1/messages", _authed(send), methods=["POST"]),
        Route("/api/v1/messages/me", _authed(my_messages), methods=["GET"]),
        Route("/api/v1/dispatch", _authed(dispatch), methods=["POST"]),
        Route("/api/v1/dispatch/claim", _authed(claim), methods=["POST"]),
        Route("/api/v1/dispatch/{job_id}/finish", _authed(finish), methods=["POST"]),
        Route("/api/v1/bootstrap", _authed(bootstrap), methods=["GET"]),
    ]
