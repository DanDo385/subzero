"""HTTP routes for coding-agent hooks, the subzero CLI and the board.

Device tokens paired with ``subzero pair-device --agents`` (then
``subzero connect``); a browser extension's token is refused with 403. Every route answers 401 to a missing or revoked token
before doing anything else, so an unauthenticated caller learns nothing.

    POST /api/v1/agents/start                 register or revive a session
    POST /api/v1/agents/{id}/beat             heartbeat; new messages if deliver=true
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
from functools import partial
from typing import Any
from uuid import UUID

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from subzero.agents.service import AgentService
from subzero.db.pool import Database
from subzero.errors import NotFoundError, ValidationError
from subzero.ingest import _bearer_token, _validate_device_token
from subzero.retrieval.service import RetrievalService

Handler = Callable[[Request, dict[str, Any]], Awaitable[Any]]


def _authed(db: Database, handler: Handler) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def endpoint(request: Request) -> JSONResponse:
        token = _bearer_token(request)
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        device = await _validate_device_token(db, token)
        if device is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if "agents" not in (device.get("scopes") or []):
            # A browser extension's token can save chats, not drive agents.
            return JSONResponse(
                {"error": "this device is not paired for agents; use pair-device --agents"},
                status_code=403,
            )
        try:
            return JSONResponse(await handler(request, device))
        except ValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (NotFoundError, asyncpg.ForeignKeyViolationError) as exc:
            detail = str(exc) if isinstance(exc, NotFoundError) else "No such session or job."
            return JSONResponse({"error": detail}, status_code=404)

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


async def _session(agents: AgentService, value: Any, name: str = "session_id") -> UUID:
    """A full session id, or the 8-character one the master prompt gives each session."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a session id.")
    try:
        return UUID(value)
    except ValueError:
        return UUID(str((await agents.resolve_session(value))["id"]))


def _str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be text.")
    return value


def _int(body: dict[str, Any], key: str) -> int | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{key} must be a whole number.")
    return value


async def start(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await agents.start(
        harness=_str(body, "harness") or "unknown",
        external_id=_str(body, "external_id"),
        name=_str(body, "name"),
        machine=_str(body, "machine") or device.get("device_name"),
        cwd=_str(body, "cwd"),
        branch=_str(body, "branch"),
        home=_str(body, "home"),
        task=_str(body, "task"),
    )


async def beat(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await agents.beat(
        await _session(agents, request.path_params["session_id"]),
        status=_str(body, "status"),
        task=_str(body, "task"),
        home=_str(body, "home"),
        deliver=body.get("deliver") is True,
    )


async def end(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await agents.end(
        await _session(agents, request.path_params["session_id"]), summary=_str(body, "summary")
    )


async def board(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    try:
        after = int(request.query_params.get("after", "0"))
    except ValueError as exc:
        raise ValidationError("after must be a number.") from exc
    return await agents.board(after_event=max(0, after))


async def send(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    sender = _str(body, "from_session")
    label, sender_id = "me", None
    if sender:
        row = await agents.resolve_session(sender)
        label, sender_id = row["name"], UUID(str(row["id"]))
    return await agents.send(
        to=_str(body, "to") or "",
        body=_str(body, "body") or "",
        from_session=sender_id,
        from_label=label,
    )


async def my_messages(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    mark = request.query_params.get("mark_read", "true") != "false"
    return {"messages": await agents.messages_for_me(mark_read=mark)}


async def dispatch(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await agents.dispatch(
        _str(body, "goal") or "",
        mode=_str(body, "mode") or "scout",
        target=_str(body, "target"),
        home=_str(body, "home"),
        space=_str(body, "space"),
        worktree=_str(body, "worktree"),
        created_by=_str(body, "created_by") or str(device.get("device_name") or "cli"),
    )


async def claim(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    targets = body.get("targets") or []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise ValidationError("targets must be a list of agent names.")
    job = await agents.claim(await _session(agents, body.get("session_id")), targets=targets)
    return {"job": job}


async def finish(agents: AgentService, request: Request, device: dict[str, Any]) -> Any:
    body = await _body(request)
    return await agents.finish(
        _uuid(request.path_params["job_id"]),
        status=_str(body, "status") or "done",
        result=_str(body, "result"),
        exit_code=_int(body, "exit_code"),
        output=_str(body, "output"),
    )


async def bootstrap(retrieval: RetrievalService, request: Request, device: dict[str, Any]) -> Any:
    params = request.query_params
    try:
        budget = min(4000, max(200, int(params.get("budget", "1200"))))
    except ValueError as exc:
        raise ValidationError("budget must be a number.") from exc
    package = await retrieval.bootstrap(
        params.get("q") or None,
        project=params.get("project") or None,
        home=params.get("home") or None,
        token_budget=budget,
    )
    return package.model_dump(mode="json", exclude_none=True)


def build_agent_routes(
    *, db: Database, agents: AgentService, retrieval: RetrievalService
) -> list[Route]:
    def authed(handler: Callable[..., Awaitable[Any]], dep: Any) -> Any:
        return _authed(db, partial(handler, dep))

    return [
        Route("/api/v1/agents/start", authed(start, agents), methods=["POST"]),
        Route("/api/v1/agents/board", authed(board, agents), methods=["GET"]),
        Route("/api/v1/agents/{session_id}/beat", authed(beat, agents), methods=["POST"]),
        Route("/api/v1/agents/{session_id}/end", authed(end, agents), methods=["POST"]),
        Route("/api/v1/messages", authed(send, agents), methods=["POST"]),
        Route("/api/v1/messages/me", authed(my_messages, agents), methods=["GET"]),
        Route("/api/v1/dispatch", authed(dispatch, agents), methods=["POST"]),
        Route("/api/v1/dispatch/claim", authed(claim, agents), methods=["POST"]),
        Route("/api/v1/dispatch/{job_id}/finish", authed(finish, agents), methods=["POST"]),
        Route("/api/v1/bootstrap", authed(bootstrap, retrieval), methods=["GET"]),
    ]
