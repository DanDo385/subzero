"""Dispatch work to coding agents and message them, from any chat app."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.mcp.result import ToolResult, object_output_schema, text_and_structured
from subzero.services import get_services


def register(mcp: FastMCP) -> None:
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
            Field(
                description=(
                    "The job, in the user's words, sigils included: '%Code/site add dark "
                    "mode >claude', '^zig build an arena allocator lesson'. A '>name' "
                    "picks the agent; without one, Jev picks."
                )
            ),
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
    ) -> ToolResult:
        """Queue a job for the user's coding agents (firstmate crew, Claude Code, ...).

        Call when the user asks to have an agent do something on their
        machines: "/dispatch ...", "get the crew to ...", "have claude fix ...".
        The job is queued, not started: tell the user it is queued and which
        agent it went to. Their board shows it being picked up.
        """
        principal = current_principal()
        job = await get_services().agents.dispatch(
            goal, mode=mode, created_by=principal.provider or "chat"
        )
        line = f"Queued for {job['target']} as {job['mode']} ({job['routed_by']})."
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
                    "Who gets it: a session name or id from agents_status, a home sigil "
                    "(^cfa2, %Code/site, &herdr) for every agent working there, or 'me'."
                )
            ),
        ],
        body: Annotated[str, Field(description="The message, under 4,000 characters.")],
    ) -> ToolResult:
        """Send a message to a running coding session. It sees it on its next prompt."""
        principal = current_principal()
        message = await get_services().agents.send(
            to=to, body=body, from_label=principal.provider or "chat"
        )
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
        board = await get_services().agents.board()
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
