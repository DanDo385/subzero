"""Per-request facts that the HTTP request log reports when the request ends.

An MCP tool that fails still answers HTTP 200 (the error is inside the
JSON-RPC result), so the request log alone cannot tell a failed tool from a
working one. :class:`subzero.server.RequestLogMiddleware` opens a record for
each request; the tool error middleware appends to it; the log line includes
it. The record is a list held in a context variable and mutated in place, so
a value appended deeper in the call reaches the middleware that created it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_tool_failures: ContextVar[list[dict[str, Any]] | None] = ContextVar("tool_failures", default=None)


def open_record() -> Token[list[dict[str, Any]] | None]:
    return _tool_failures.set([])


def close_record(token: Token[list[dict[str, Any]] | None]) -> list[dict[str, Any]]:
    failures = _tool_failures.get() or []
    _tool_failures.reset(token)
    return failures


def record_tool_failure(tool: str, error_class: str, kind: str) -> None:
    failures = _tool_failures.get()
    if failures is not None:
        failures.append({"tool": tool, "error_class": error_class, "error_kind": kind})
