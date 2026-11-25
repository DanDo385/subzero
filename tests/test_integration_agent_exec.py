"""Executable jobs: the agents:exec scope gate, cwd resolution, and the result path.

The server side only. Nothing here executes a command; jobs are rows, and the
runner that claims them lives in a later slice.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from conftest import requires_db
from subzero.agents.service import OUTPUT_TAIL_CHARS
from subzero.errors import ValidationError

pytestmark = requires_db


@pytest.fixture
def code_root(services, tmp_path: Path) -> Path:
    """A stand-in ~/Code with a checkout and a worktree, as the exec roots."""
    code = tmp_path / "Code"
    (code / "subzero").mkdir(parents=True)
    (code / "worktrees" / "subzero" / "cli-exec").mkdir(parents=True)
    services.settings.exec_roots = [str(code), str(code / "worktrees")]
    return code.resolve()


@pytest.fixture
async def client(services, live_server):
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as mcp:
        yield mcp


@pytest.fixture
async def http(services, live_server):
    """A device token paired for agents, like the runner on the machine would use."""
    async with httpx.AsyncClient(base_url=str(live_server.base_url)) as paired_client:
        await services.db.execute(
            "INSERT INTO pairing_codes (code, device_name, scopes) "
            "VALUES ('EXECCODE', 'mbp', '{ingest,agents}')"
        )
        paired = await paired_client.post(
            "/api/v1/auth/pair-device", json={"pairing_code": "EXECCODE", "device_name": "mbp"}
        )
        token = paired.json()["device_token"]
        paired_client.headers["authorization"] = f"Bearer {token}"
        yield paired_client


# ---------------------------------------------------------------- scope gate


async def test_exec_tools_are_advertised_with_the_scope_and_refused_without(
    client, services, code_root
) -> None:
    """agents:exec is not a :read scope: a read-only policy hides the tools,
    a direct call is refused, and no job row appears. The positive half keeps
    the test honest: with the scope the tools really are registered."""
    names = {tool.name for tool in await client.list_tools()}
    assert {"agent_run", "agent_open"} <= names  # the static token holds every scope

    services.settings.client_scope_policy = {"*": ["read"]}
    names = {tool.name for tool in await client.list_tools()}
    assert "agent_run" not in names and "agent_open" not in names
    with pytest.raises(ToolError) as refused:
        await client.call_tool("agent_run", {"command": "whoami"})
    assert "agent_run" in str(refused.value)
    assert await services.db.fetchval("SELECT count(*) FROM dispatch_jobs") == 0


# ------------------------------------------------------------- queueing


async def test_agent_run_queues_a_command_job_with_a_resolved_cwd(
    client, services, code_root
) -> None:
    result = await client.call_tool(
        "agent_run",
        {"command": "uv run pytest -q", "worktree": "subzero/cli-exec", "timeout_s": 60},
    )
    assert "Queued" in result.content[0].text
    assert result.data["kind"] == "command"

    worktree_cwd = str(code_root / "worktrees" / "subzero" / "cli-exec")
    job = await services.db.fetchrow("SELECT * FROM dispatch_jobs")
    assert job["status"] == "queued"
    assert job["kind"] == "command"
    assert job["command"] == "uv run pytest -q"
    assert job["goal"] == "uv run pytest -q"  # the board shows what will run
    assert job["worktree"] == "subzero/cli-exec"
    assert job["cwd"] == worktree_cwd
    assert job["timeout_s"] == 60
    assert job["keep_open"] is False
    assert (job["target"], job["routed_by"]) == ("crew", "default")

    audit_row = await services.db.fetchrow("SELECT * FROM audit_log WHERE tool = 'agent_run'")
    assert audit_row is not None
    assert audit_row["action"] == "create"
    assert audit_row["object_id"] == job["id"]
    assert audit_row["metadata"] == {"cwd": worktree_cwd, "timeout_s": 60, "target": "crew"}


async def test_agent_open_queues_a_session_that_keeps_open(client, services, code_root) -> None:
    await client.call_tool(
        "agent_open",
        {"worktree": "subzero", "first_command": "uv run pytest", "target": "claude"},
    )
    job = await services.db.fetchrow("SELECT * FROM dispatch_jobs")
    assert job["kind"] == "session"
    assert job["keep_open"] is True
    assert job["command"] is None  # the schema reserves it for kind='command'
    assert job["first_command"] == "uv run pytest"  # typed for the human, never executed
    assert job["goal"] == "Open an interactive session in subzero."  # the description
    assert job["cwd"] == str(code_root / "subzero")
    assert (job["target"], job["routed_by"]) == ("claude", "explicit")

    audit_row = await services.db.fetchrow("SELECT * FROM audit_log WHERE tool = 'agent_open'")
    assert audit_row is not None
    assert audit_row["metadata"]["target"] == "claude"


async def test_a_session_without_a_first_command_says_what_to_open(services, code_root) -> None:
    job = await services.agents.queue_session(worktree="subzero/cli-exec", space="ws-1")
    assert job["goal"] == "Open an interactive session in subzero/cli-exec."
    assert job["space"] == "ws-1"
    assert job["first_command"] is None
    assert job["keep_open"] is True


async def test_the_schema_refuses_first_command_on_a_command_job(services, code_root) -> None:
    """The CHECK is the last gate: first_command belongs to kind='session' only."""
    job = await services.agents.queue_command("true", worktree="subzero")
    assert job["first_command"] is None
    with pytest.raises(asyncpg.CheckViolationError):
        await services.db.execute(
            "UPDATE dispatch_jobs SET first_command = 'ls' WHERE id = $1", UUID(job["id"])
        )


async def test_over_long_command_bad_timeout_and_bad_worktree_are_refused(
    client, services, code_root
) -> None:
    with pytest.raises(ToolError):
        await client.call_tool("agent_run", {"command": "x" * 4001})
    with pytest.raises(ToolError):
        await client.call_tool("agent_run", {"command": "true", "timeout_s": 1801})
    with pytest.raises(ToolError):
        await client.call_tool("agent_run", {"command": "true", "worktree": "nope/missing"})
    assert await services.db.fetchval("SELECT count(*) FROM dispatch_jobs") == 0


async def test_the_service_refuses_bad_inputs_before_any_schema(services, code_root) -> None:
    """The HTTP path has no tool schema: the service is the enforcement."""
    with pytest.raises(ValidationError):
        await services.agents.queue_command("   ")
    with pytest.raises(ValidationError):
        await services.agents.queue_command("x" * 4001)
    with pytest.raises(ValidationError):
        await services.agents.queue_command("true", timeout_s=0)
    with pytest.raises(ValidationError):
        await services.agents.queue_command("true", timeout_s=1801)
    with pytest.raises(ValidationError):
        await services.agents.queue_command("true", target="nobody")
    with pytest.raises(ValidationError):
        await services.agents.queue_session(first_command="x" * 4001)
    assert await services.db.fetchval("SELECT count(*) FROM dispatch_jobs") == 0


# ------------------------------------------------------------- dispatch


async def test_dispatch_accepts_a_worktree_and_stores_the_resolved_cwd(services, code_root) -> None:
    job = await services.agents.dispatch(
        "finish the cutover", target="claude", worktree="subzero/cli-exec", space="ws-1"
    )
    assert job["kind"] == "goal"
    assert job["worktree"] == "subzero/cli-exec"
    assert job["cwd"] == str(code_root / "worktrees" / "subzero" / "cli-exec")
    assert job["space"] == "ws-1"
    assert job["command"] is None


async def test_dispatch_refuses_a_worktree_outside_the_roots(services, code_root, tmp_path) -> None:
    with pytest.raises(ValidationError):
        await services.agents.dispatch("do it", target="claude", worktree=str(tmp_path))
    assert await services.db.fetchval("SELECT count(*) FROM dispatch_jobs") == 0


# ------------------------------------------------------------- result path


async def test_finish_stores_a_capped_tail_and_flags_the_truncation(services, code_root) -> None:
    job = await services.agents.queue_command("make noise", worktree="subzero/cli-exec")
    output = "".join(f"{i:06d}\n" for i in range(3000))  # 21,000 chars: over the cap
    assert len(output) > OUTPUT_TAIL_CHARS
    done = await services.agents.finish(
        UUID(job["id"]), status="failed", exit_code=3, output=output
    )
    assert done["exit_code"] == 3
    assert done["output"] == output[-OUTPUT_TAIL_CHARS:]
    assert len(done["output"]) == OUTPUT_TAIL_CHARS
    assert done["output_truncated"] is True

    stored = await services.db.fetchrow(
        "SELECT exit_code, output, output_truncated FROM dispatch_jobs WHERE id = $1",
        UUID(job["id"]),
    )
    assert stored["output"] == output[-OUTPUT_TAIL_CHARS:]
    assert stored["output_truncated"] is True


async def test_a_short_output_is_stored_whole(services, code_root) -> None:
    job = await services.agents.queue_command("true")
    done = await services.agents.finish(UUID(job["id"]), exit_code=0, output="ok\n")
    assert done["output"] == "ok\n"
    assert done["output_truncated"] is False
    assert done["exit_code"] == 0


async def test_output_never_reaches_the_chat_tools(client, services, code_root) -> None:
    """output is user content: the chat-app surface does not carry it."""
    job = await services.agents.queue_command("echo hi", worktree="subzero/cli-exec")
    await services.agents.finish(UUID(job["id"]), exit_code=0, output="SECRET-TAIL")
    status = await client.call_tool("agents_status", {})
    assert "SECRET-TAIL" not in json.dumps(status.data)


# ------------------------------------------------------------- HTTP


async def test_dispatch_and_finish_over_http_carry_the_exec_fields(
    http, services, code_root
) -> None:
    queued = await http.post(
        "/api/v1/dispatch",
        json={"goal": "run the tests", "target": "claude", "worktree": "subzero/cli-exec"},
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["cwd"] == str(code_root / "worktrees" / "subzero" / "cli-exec")

    output = "boom\n" * 5000  # 25,000 chars: over the cap
    finished = await http.post(
        f"/api/v1/dispatch/{queued.json()['id']}/finish",
        json={"status": "failed", "exit_code": 3, "output": output},
    )
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["exit_code"] == 3
    assert body["output"] == output[-OUTPUT_TAIL_CHARS:]
    assert body["output_truncated"] is True

    bad = await http.post(
        f"/api/v1/dispatch/{queued.json()['id']}/finish", json={"exit_code": "three"}
    )
    assert bad.status_code == 400
    assert "whole number" in bad.json()["error"]
