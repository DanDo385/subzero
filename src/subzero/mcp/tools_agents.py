"""Dispatch work to coding agents and message them, from any chat app."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.agents.service import AgentService
from subzero.audit.service import AuditService
from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.homes import Home
from subzero.mcp.result import ToolResult, object_output_schema, text_and_structured


def register(mcp: FastMCP, *, agents: AgentService, audit: AuditService) -> None:
    @mcp.tool(
        name="dispatch",
        tags={"agents", "write"},
        auth=require_scopes(scopes.AGENTS_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
        output_schema=object_output_schema(),
    )
    async def dispatch(
        goal: Annotated[
            str,
            Field(description="The job in the user's words."),
        ],
        mode: Annotated[
            Literal["scout", "ship"],
            Field(
                description=(
                    "scout reads, plans and reports; ship makes the change. Use ship only "
                    "when the user said to go ahead. Jobs touching network, credentials, "
                    "deletion or deployment always start as scout."
                )
            ),
        ] = "scout",
        target: Annotated[
            Literal["claude", "codex", "grok", "gemini", "pi", "me", "crew"] | None,
            Field(description="Agent named by the user. Leave empty to route automatically."),
        ] = None,
        home: Annotated[
            Home | None,
            Field(description="Optional home, such as projects/portfolio-site or lesson/zig."),
        ] = None,
        space: Annotated[
            str | None,
            Field(
                description=(
                    "herdr workspace id or label the job belongs to, when the user named "
                    "one. Stored as given; herdr is the authority on whether it exists."
                )
            ),
        ] = None,
        worktree: Annotated[
            str | None,
            Field(
                description=(
                    "Where the work lives: 'repo/slug' for a worktree under "
                    "~/Code/worktrees, 'repo' for a checkout under ~/Code, or an absolute "
                    "path inside a configured exec root. Resolved on the server and "
                    "refused if it does not exist."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Queue a job for the user's coding agents (firstmate crew, Claude Code, ...).

        Call when the user asks to have an agent do something on their
        machines: "/dispatch ...", "get the crew to ...", "have claude fix ...".
        The job is queued, not started: tell the user it is queued and which
        agent it went to. Their board shows it being picked up.
        """
        principal = current_principal()
        job = await agents.dispatch(
            goal,
            mode=mode,
            target=target,
            home=home,
            created_by=principal.provider or "chat",
            space=space,
            worktree=worktree,
        )
        line = f"Queued for {job['target']} as {job['mode']} ({job['routed_by']})."
        return text_and_structured(line, job)

    @mcp.tool(
        name="agent_run",
        tags={"agents", "write"},
        auth=require_scopes(scopes.AGENTS_EXEC),
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
        output_schema=object_output_schema(),
    )
    async def agent_run(
        command: Annotated[
            str,
            Field(
                description=(
                    "The exact command to run and nothing chained onto it: no '&& rm -rf', "
                    "no force-push tail, one command per job. When the result comes back, "
                    "report the exit code honestly: a non-zero exit is a failure even when "
                    "the output looks fine."
                )
            ),
        ],
        worktree: Annotated[
            str | None,
            Field(
                description=(
                    "Where it runs: 'repo/slug' for a worktree under ~/Code/worktrees, "
                    "'repo' for a checkout under ~/Code, or an absolute path inside a "
                    "configured exec root. Refused if it does not exist."
                )
            ),
        ] = None,
        space: Annotated[
            str | None,
            Field(
                description=(
                    "herdr workspace id or label, when the command belongs to one. Stored "
                    "as given; herdr decides whether it knows it."
                )
            ),
        ] = None,
        timeout_s: Annotated[
            int,
            Field(
                ge=1,
                le=1800,
                description="Seconds the runner gives the command before killing it.",
            ),
        ] = 120,
        target: Annotated[
            str | None,
            Field(
                description=(
                    "Which agent's runner executes it (claude, codex, grok, gemini, pi, "
                    "crew). Without one, any free runner takes it."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Queue one command to run on the user's machine.

        The job is queued, not started: the server executes nothing, and the
        runner on the user's machine claims the job and does the running.
        Call when the user asks for a specific command on their own machine
        (a build, a test run, a status check). Tell the user it is queued and
        which target it went to.
        """
        principal = current_principal()
        job = await agents.queue_command(
            command,
            worktree=worktree,
            space=space,
            timeout_s=timeout_s,
            target=target,
            created_by=principal.provider or "chat",
        )
        await audit.record(
            tool="agent_run",
            action="create",
            object_type="dispatch_job",
            object_id=UUID(job["id"]),
            metadata={"cwd": job["cwd"], "timeout_s": job["timeout_s"], "target": job["target"]},
        )
        line = f"Queued for {job['target']}: the machine's runner executes it, not this server."
        return text_and_structured(line, job)

    @mcp.tool(
        name="agent_open",
        tags={"agents", "write"},
        auth=require_scopes(scopes.AGENTS_EXEC),
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
        output_schema=object_output_schema(),
    )
    async def agent_open(
        worktree: Annotated[
            str | None,
            Field(
                description=(
                    "Where the session opens: 'repo/slug' for a worktree under "
                    "~/Code/worktrees, 'repo' for a checkout under ~/Code, or an absolute "
                    "path inside a configured exec root. Refused if it does not exist."
                )
            ),
        ] = None,
        space: Annotated[
            str | None,
            Field(
                description=(
                    "herdr workspace id or label to open the session in. Stored as given; "
                    "herdr decides whether it knows it."
                )
            ),
        ] = None,
        first_command: Annotated[
            str | None,
            Field(
                description=(
                    "Text the runner types into the opened session for the human to "
                    "review. It is never executed by the runner, and the runner does not "
                    "press Enter unless the caller asked for that with a trailing newline."
                )
            ),
        ] = None,
        target: Annotated[
            str | None,
            Field(
                description=(
                    "Which agent's runner opens it (claude, codex, grok, gemini, pi, crew). "
                    "Without one, any free runner takes it."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Queue opening an interactive session on the user's machine.

        The job is queued, not started: the runner opens the session in the
        resolved folder or herdr workspace and leaves it open for the user.
        Call when the user asks to open a terminal or session somewhere. Tell
        the user it is queued and which target it went to.
        """
        principal = current_principal()
        job = await agents.queue_session(
            worktree=worktree,
            space=space,
            first_command=first_command,
            target=target,
            created_by=principal.provider or "chat",
        )
        await audit.record(
            tool="agent_open",
            action="create",
            object_type="dispatch_job",
            object_id=UUID(job["id"]),
            metadata={"cwd": job["cwd"], "timeout_s": job["timeout_s"], "target": job["target"]},
        )
        line = f"Queued for {job['target']}: the runner opens the session on the machine."
        return text_and_structured(line, job)

    @mcp.tool(
        name="agent_message",
        tags={"agents", "write"},
        auth=require_scopes(scopes.AGENTS_WRITE),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        output_schema=object_output_schema(),
    )
    async def agent_message(
        to: Annotated[
            str,
            Field(
                description=(
                    "Who gets it: a session name or id from agents_status, a home such as "
                    "lesson/cfa2 for every agent working there, or 'me'."
                )
            ),
        ],
        body: Annotated[str, Field(description="The message, under 4,000 characters.")],
    ) -> ToolResult:
        """Send a message to a running coding session. It sees it on its next prompt."""
        principal = current_principal()
        message = await agents.send(to=to, body=body, from_label=principal.provider or "chat")
        return text_and_structured("Sent.", message)

    @mcp.tool(
        name="agents_status",
        tags={"agents", "read"},
        auth=require_scopes(scopes.AGENTS_READ),
        annotations={"readOnlyHint": True},
        output_schema=object_output_schema(),
    )
    async def agents_status() -> ToolResult:
        """Which agents are running, what each is doing, open jobs, and messages for the user.

        Call when the user asks what their agents are doing, whether a job is
        done, or if anyone is waiting on them. Sessions with status 'waiting'
        need the user (for example, to type code they are learning).
        """
        board = await agents.board()
        live = [s for s in board["sessions"] if s["status"] != "done"]
        waiting = [s for s in live if s["status"] == "waiting"]
        open_jobs = [j for j in board["jobs"] if j["status"] in ("queued", "claimed")]
        line = (
            f"{len(live)} running, {len(waiting)} waiting on you, "
            f"{len(open_jobs)} open jobs, {len(board['inbox'])} messages for you."
        )
        return text_and_structured(
            line,
            {
                "sessions": [
                    {
                        k: s[k]
                        for k in ("id", "name", "harness", "machine", "home", "status", "task")
                    }
                    for s in live
                ],
                "jobs": [
                    {k: j[k] for k in ("id", "goal", "target", "mode", "status", "result")}
                    for j in open_jobs
                ],
                "messages_for_you": [
                    {k: m[k] for k in ("from_label", "body", "created_at")} for m in board["inbox"]
                ],
            },
        )
