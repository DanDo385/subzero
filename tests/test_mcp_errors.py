"""What a client is shown for each kind of tool failure (no database needed)."""

from __future__ import annotations

import asyncpg

from subzero.errors import NotFoundError, ValidationError
from subzero.logging_setup import request_id_var
from subzero.mcp.errors import readable_error


def test_caller_errors_keep_their_message() -> None:
    assert readable_error(NotFoundError("No project matches 'x'."), expose_details=False) == (
        "No project matches 'x'.",
        "caller",
    )
    message, kind = readable_error(ValidationError("worktree is not allowed"), expose_details=False)
    assert (message, kind) == ("worktree is not allowed", "caller")


def test_a_schema_error_names_the_fix_not_the_sql() -> None:
    token = request_id_var.set("req-123")
    try:
        exc = asyncpg.exceptions.UndefinedColumnError("column x.provenance does not exist")
        message, kind = readable_error(exc, expose_details=False)
    finally:
        request_id_var.reset(token)
    assert kind == "schema"
    assert "subzero migrate" in message
    assert "req-123" in message
    assert "provenance" not in message


def test_a_timeout_asks_for_a_retry() -> None:
    message, kind = readable_error(TimeoutError(), expose_details=False)
    assert kind == "timeout"
    assert "Retry" in message


def test_an_unknown_error_hides_its_details_in_production() -> None:
    exc = RuntimeError("connection to postgres://user:secret@host failed")
    hidden, kind = readable_error(exc, expose_details=False)
    assert kind == "internal"
    assert "secret" not in hidden
    assert "Reference" in hidden

    shown, _ = readable_error(exc, expose_details=True)
    assert "RuntimeError" in shown
