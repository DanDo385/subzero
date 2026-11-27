"""The Mac CLI runner: real tmux sessions on a private socket, and the scope gate.

No database. The socket and job files stay under the test's temp directory,
so the user's default tmux server is never listed or killed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.authorization import AuthContext, require_scopes

from subzero.auth import scopes
from subzero.cli_host.runner import CliHostRunner
from subzero.config import Settings
from subzero.errors import ValidationError
from subzero.mcp import tools_cli


@pytest.fixture
def host(tmp_path: Path) -> Iterator[tuple[CliHostRunner, Path]]:
    work = tmp_path / "work"
    work.mkdir()
    runner = CliHostRunner(
        roots=[str(work)],
        state_dir=tmp_path / "state",
        socket_name="sz" + uuid.uuid4().hex[:10],
        sync_wait_s=5,
    )
    try:
        yield runner, work
    finally:
        runner.kill_server()


def test_exit_code_7_is_reported(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    result = runner.run("exit 7", cwd=str(work))

    assert result["status"] == "done", result["stderr"]
    assert result["exit_code"] == 7
    assert result["cwd"] == str(work.resolve())
    assert result["host"]
    assert result["session"] not in runner.sessions()


def test_timed_out_command_is_stopped(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    result = runner.run("sleep 30", cwd=str(work), timeout_s=1)

    assert result["status"] == "timed_out"
    assert result["session"] not in runner.sessions()
    pid_path = runner.state_dir / "jobs" / str(result["job_id"]) / "command_pid"
    pid = int(pid_path.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_two_requests_get_separate_sessions(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    first = runner.start("sleep 20", cwd=str(work), timeout_s=30)
    second = runner.start("sleep 20", cwd=str(work), timeout_s=30)
    try:
        names = runner.sessions()
        assert first["session"] in names
        assert second["session"] in names
        assert first["session"] != second["session"]
        assert first["job_id"] != second["job_id"]
    finally:
        runner.cleanup(str(first["job_id"]))
        runner.cleanup(str(second["job_id"]))


def test_sessions_stay_off_the_default_tmux_server(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    import subprocess

    started = runner.start("sleep 20", cwd=str(work), timeout_s=30)
    try:
        default = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert str(started["session"]) not in default.stdout
        assert str(started["session"]) in runner.sessions()
    finally:
        runner.cleanup(str(started["job_id"]))


def test_finished_session_is_removed_unless_retained(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    kept = runner.run("echo hello-from-cli", cwd=str(work), retain=True)

    assert kept["status"] == "done"
    assert kept["exit_code"] == 0
    assert "hello-from-cli" in str(kept["stdout"])
    assert kept["retained"] is True
    assert kept["session"] in runner.sessions()
    assert "hello-from-cli" in runner.capture(str(kept["session"]))

    runner.cleanup(str(kept["job_id"]))
    assert kept["session"] not in runner.sessions()


def test_command_does_not_inherit_server_environment(
    host: tuple[CliHostRunner, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, work = host
    monkeypatch.setenv("SUBZERO_SENTINEL", "secret-value")
    result = runner.run(
        '/usr/bin/python3 -c \'import os; print(os.environ.get("SUBZERO_SENTINEL", "<absent>"))\'',
        cwd=str(work),
    )

    assert result["status"] == "done"
    assert result["exit_code"] == 0
    assert "<absent>" in str(result["stdout"])
    assert "secret-value" not in str(result["stdout"])
    assert "SUBZERO_SENTINEL" not in str(result["stdout"])


def test_sudo_and_paths_outside_the_roots_are_refused(host: tuple[CliHostRunner, Path]) -> None:
    runner, work = host
    with pytest.raises(ValidationError, match="sudo"):
        runner.run("sudo true", cwd=str(work))
    with pytest.raises(ValidationError, match="outside"):
        runner.run("pwd", cwd="/")
    assert runner.sessions() == []


def test_cli_run_scope_rejects_a_token_without_it() -> None:
    check = require_scopes(scopes.CLI_RUN)
    denied = AuthContext(
        token=AccessToken(
            token="t",
            client_id="c",
            scopes=[scopes.CONTEXT_READ],
            expires_at=None,
            subject="me@example.com",
        ),
        component=object(),  # type: ignore[arg-type]
    )
    assert check(denied) is False
    assert check(AuthContext(token=None, component=object())) is False  # type: ignore[arg-type]
    allowed = AuthContext(
        token=AccessToken(
            token="t",
            client_id="c",
            scopes=[scopes.CLI_RUN],
            expires_at=None,
            subject="me@example.com",
        ),
        component=object(),  # type: ignore[arg-type]
    )
    assert check(allowed) is True


async def test_unauthorized_mcp_request_is_rejected() -> None:
    """An unauthenticated caller cannot invoke the tool.

    The in-memory client has no auth channel, so a token cannot be presented.
    A missing token is the unauthenticated request. A token that lacks
    ``cli:run`` is rejected by the same ``require_scopes`` check covered above.
    """
    mcp = FastMCP(
        auth=StaticTokenVerifier(
            tokens={
                "runner": {
                    "client_id": "runner",
                    "scopes": [scopes.CLI_RUN],
                    "sub": "me@example.com",
                }
            }
        )
    )
    tools_cli.register(mcp, audit=None, settings=Settings())  # type: ignore[arg-type]

    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("run_cli_on_mbp", {"command": "uname -s"})
