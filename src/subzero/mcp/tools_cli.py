"""Run one explicit command on this Mac and return the result in the same chat.

The tool is the whole request surface. It does not add an HTTP route.
``cli:run`` is granted by name only, the same way ``agents:exec`` is.
Stdout and stderr are data: they are returned to the caller and are not
written to the audit row, the classifier or the embedder.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.audit.service import AuditService
from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.cli_host.runner import runner_for, runs_on_this_host
from subzero.config import Settings
from subzero.errors import ValidationError
from subzero.mcp.result import ToolResult, object_output_schema, text_and_structured


def register(mcp: FastMCP, *, audit: AuditService, settings: Settings) -> None:
    @mcp.tool(
        name="run_cli_on_mbp",
        tags={"cli", "write"},
        auth=require_scopes(scopes.CLI_RUN),
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
        output_schema=object_output_schema(),
    )
    async def run_cli_on_mbp(
        command: Annotated[
            str,
            Field(
                description=(
                    "The user's exact command, and nothing you added. One command. "
                    "Do not prefix sudo, do not chain on a cleanup command, and do not "
                    "invent a command the user did not ask for. A non-zero exit code is "
                    "the result: report it, do not treat the output as success."
                )
            ),
        ],
        cwd: Annotated[
            str | None,
            Field(
                description=(
                    "Working directory: 'repo/slug' under ~/Code/worktrees, 'repo' under "
                    "~/Code, or an absolute path inside a configured exec root. Refused "
                    "if it does not exist. Omit only when the user did not name a directory."
                )
            ),
        ] = None,
        timeout_s: Annotated[
            int,
            Field(
                ge=1,
                le=600,
                description=(
                    "Seconds before the command is stopped. Short commands return in this "
                    "call. A command that is still running when the wait elapses returns "
                    "its job id; call cli_job_on_mbp for the result."
                ),
            ),
        ] = 30,
        retain: Annotated[
            bool,
            Field(
                description=(
                    "Leave the tmux session after the command so the user can reattach. "
                    "Default false: the session is removed once the result is recorded. "
                    "Set true only when the user asked to reattach."
                )
            ),
        ] = False,
    ) -> ToolResult:
        """Run one command on this Mac and return its exit code and output.

        Call only when the user asked for a specific command on this Mac and
        wants the result in this chat. Do not use dispatch or agent_run for
        that: those queue work for a coding harness and do not return the
        command's output here. Each call is a new tmux session on the runner's
        own socket, not an existing terminal.
        """
        principal = current_principal()
        if principal.is_anonymous:
            raise ValidationError("run_cli_on_mbp requires an authenticated caller.")
        if not runs_on_this_host(settings):
            raise ValidationError(
                "This process is not the Mac CLI runner, so no shell was started. "
                "A server that is not on the Mac does not run commands; no outbound "
                "worker is registered on this process."
            )
        result = await asyncio.to_thread(
            runner_for(settings).run,
            command,
            cwd=cwd,
            timeout_s=timeout_s,
            retain=retain,
        )
        await audit.record(
            tool="run_cli_on_mbp",
            action="run",
            object_type="cli_job",
            object_id=UUID(str(result["job_id"])),
            metadata={
                "host": result["host"],
                "cwd": result["cwd"],
                "status": result["status"],
                "exit_code": result["exit_code"],
                "timeout_s": timeout_s,
                "retain": retain,
            },
        )
        return text_and_structured(_line(result), result)

    @mcp.tool(
        name="cli_job_on_mbp",
        tags={"cli", "read"},
        auth=require_scopes(scopes.CLI_RUN),
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        output_schema=object_output_schema(),
    )
    async def cli_job_on_mbp(
        job_id: Annotated[
            str,
            Field(description="The job id returned by run_cli_on_mbp."),
        ],
    ) -> ToolResult:
        """Read a command started by run_cli_on_mbp.

        Call when that tool returned status running, or when the user asks
        about a job id. The output is the command's own stdout and stderr.
        """
        principal = current_principal()
        if principal.is_anonymous:
            raise ValidationError("cli_job_on_mbp requires an authenticated caller.")
        if not runs_on_this_host(settings):
            raise ValidationError(
                "This process is not the Mac CLI runner, so it has no local job to read."
            )
        result = await asyncio.to_thread(runner_for(settings).status, job_id)
        return text_and_structured(_line(result), result)


def _line(result: dict[str, object]) -> str:
    status = str(result["status"])
    host = result["host"]
    cwd = result["cwd"]
    job_id = result["job_id"]
    if status == "done":
        return f"Exit {result['exit_code']} on {host} in {cwd}. Job {job_id}."
    if status == "running":
        return (
            f"Running on {host} in {cwd} as job {job_id}. "
            "Call cli_job_on_mbp with that job id for the result."
        )
    if status == "timed_out":
        return f"Timed out and stopped on {host} in {cwd}. Job {job_id}."
    return f"Did not run on {host} in {cwd}. Job {job_id}."
