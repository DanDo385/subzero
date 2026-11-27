"""Turn exceptions from a tool into errors a client can read and act on.

In production FastMCP masks every exception that is not a ``ToolError``, so a
client sees only "Error calling tool 'context_search'". Gemini and ChatGPT
then report it as INVALID_REQUEST / INVALID_ARGUMENT, which points the user at
their own arguments. That hid a day-long outage (the database schema was
behind the code) behind messages nobody could act on.

This middleware runs around every tool call and decides what the caller may
see. Masking stays on for everything it does not recognise:

* ``SubzeroError`` (NotFoundError, ValidationError, RateLimited, ...): its
  message is written for the caller, so it is returned as is.
* A missing column, table or function in Postgres: the database schema is
  behind this server. Say so, with the request id, so the operator knows to
  run ``subzero migrate``.
* A database timeout: say the database was slow, so the caller retries.
* Anything else: "Internal error (reference <request id>)". The traceback is
  logged under that request id; nothing internal (SQL, paths, connection
  details) reaches the client. Outside production the exception text is
  appended to help local debugging.

FastMCP's own errors (argument validation, unknown tool, ToolError raised by
another middleware) pass through untouched: they are already readable.

Every failure is also logged as one structured ``tool failed`` line and
recorded on the request, so the HTTP request log shows the failed tool even
though the HTTP status is 200.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastmcp.exceptions import DisabledError, FastMCPError, ToolError
from fastmcp.exceptions import NotFoundError as FastMCPNotFound
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError as PydanticValidationError

from subzero.db import schema_status
from subzero.errors import SubzeroError
from subzero.logging_setup import get_logger, request_id_var, tool_var
from subzero.request_context import record_tool_failure

logger = get_logger(__name__)

#: Raised by Postgres when the code asks for something the schema lacks.
_SCHEMA_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.exceptions.UndefinedColumnError,
    asyncpg.exceptions.UndefinedTableError,
    asyncpg.exceptions.UndefinedFunctionError,
    asyncpg.exceptions.UndefinedObjectError,
)

#: Already readable, or not ours to rewrite.
_PASS_THROUGH: tuple[type[BaseException], ...] = (
    FastMCPError,
    FastMCPNotFound,
    DisabledError,
    MCPError,
    PydanticValidationError,
)


def _schema_message(reference: str) -> str:
    pending = schema_status.cached()
    detail = f" ({len(pending)} migration(s) pending)" if pending else ""
    return (
        "Subzero's database schema is behind this server's code" + detail + ", so this "
        "tool cannot run. This is a server problem, not your arguments: the operator "
        f"must run `subzero migrate`. Reference {reference}."
    )


def readable_error(exc: BaseException, *, expose_details: bool) -> tuple[str, str]:
    """The message a caller sees for ``exc``, and the kind recorded in logs."""
    reference = request_id_var.get()
    if isinstance(exc, SubzeroError):
        return str(exc) or type(exc).__name__, "caller"
    if isinstance(exc, _SCHEMA_ERRORS):
        return _schema_message(reference), "schema"
    if isinstance(exc, TimeoutError | asyncpg.exceptions.QueryCanceledError):
        return (
            "Subzero's database took too long to answer. Retry in a moment; if it keeps "
            f"happening, ask for a smaller result. Reference {reference}.",
            "timeout",
        )
    message = f"Internal error in Subzero. Reference {reference}."
    if expose_details:
        message += f" ({type(exc).__name__}: {exc})"
    return message, "internal"


def _original(raised: BaseException) -> BaseException | None:
    """The exception to translate, or None to let ``raised`` through as it is.

    FastMCP catches a tool's exception itself and re-raises it as
    ``ToolError("Error calling tool ...")`` with the original as ``__cause__``,
    so that is what a middleware sees. A ToolError a tool or middleware raised
    on purpose has no such cause, and FastMCP's own errors are already
    readable; both pass through.
    """
    if isinstance(raised, ToolError):
        cause = raised.__cause__
        if cause is None or isinstance(cause, _PASS_THROUGH):
            return None
        return cause
    if isinstance(raised, _PASS_THROUGH):
        return None
    return raised


class ReadableErrorsMiddleware(Middleware):
    def __init__(self, *, expose_details: bool) -> None:
        self._expose_details = expose_details

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        tool = getattr(context.message, "name", None) or "?"
        # Every log line written during the call carries the tool's name.
        token = tool_var.set(tool)
        try:
            return await call_next(context)
        except Exception as raised:
            exc = _original(raised)
            if exc is None:
                raise
            message, kind = readable_error(exc, expose_details=self._expose_details)
            fields: dict[str, Any] = {"error_class": type(exc).__name__, "error_kind": kind}
            if kind == "schema":
                # The error says *what* is missing; it is not user content.
                fields["error"] = str(exc)[:300]
                fields["pending_migrations"] = schema_status.cached()
            if kind in ("caller", "timeout"):
                logger.warning("tool failed", extra=fields)
            else:
                logger.error("tool failed", extra=fields, exc_info=exc)
            record_tool_failure(tool, type(exc).__name__, kind)
            raise ToolError(message) from exc
        finally:
            tool_var.reset(token)
