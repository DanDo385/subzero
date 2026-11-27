"""Project tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from subzero.audit.service import AuditService
from subzero.auth import scopes
from subzero.auth.principal import current_principal
from subzero.models import ProjectRecord
from subzero.projects.service import ProjectService


def register(mcp: FastMCP, *, audit: AuditService, projects: ProjectService) -> None:
    # ------------------------------------------------------------ projects

    @mcp.tool(
        name="project_list",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def project_list(
        status: Annotated[
            str | None,
            Field(description="active, paused, done, archived, or null for all."),
        ] = "active",
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum results to return.")] = 50,
    ) -> list[ProjectRecord]:
        """The user's projects, most recently active first."""
        return await projects.list_all(status=status, limit=limit)

    @mcp.tool(
        name="project_search",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def project_search(
        query: Annotated[str, Field(description="Words from the project name.")],
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results to return.")] = 10,
    ) -> list[ProjectRecord]:
        """Find a project by name. Call before project_create."""
        return await projects.search(query, limit=limit)

    @mcp.tool(
        name="project_get",
        tags={"projects", "read"},
        auth=require_scopes(scopes.PROJECTS_READ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def project_get(
        project: Annotated[str, Field(description="Project slug or name.")],
    ) -> dict:
        """Full current state of one project: goals, live decisions, open tasks
        and recent activity. This is the fastest way to resume work on it."""
        project_id = await projects.require(project)
        return await projects.snapshot(project_id)

    @mcp.tool(
        name="project_create",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def project_create(
        name: Annotated[str, Field(description="Project name, as the user says it.")],
        description: Annotated[
            str | None, Field(description="What the project is, in a sentence or two.")
        ] = None,
        goals: Annotated[str | None, Field(description="What finishing it looks like.")] = None,
    ) -> ProjectRecord:
        """Create a project. Call project_search first - a second project for
        work that already has one splits its history."""
        principal = current_principal()
        project_id, created = await projects.create(
            name=name,
            description=description,
            goals=goals,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        await audit.record(
            tool="project_create",
            action="create" if created else "read",
            object_type="project",
            object_id=project_id,
            principal=principal,
        )
        return await projects.get(str(project_id))

    @mcp.tool(
        name="project_update",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def project_update(
        project: Annotated[str, Field(description="Project slug or name.")],
        status: Annotated[
            str | None, Field(description="active, paused, done or archived.")
        ] = None,
        goals: Annotated[
            str | None,
            Field(
                description="Replacement goals: what finishing it looks like. Omit to keep the current goals."
            ),
        ] = None,
        description: Annotated[
            str | None, Field(description="Replacement description. Omit to keep the current one.")
        ] = None,
    ) -> ProjectRecord:
        """Change a project's status, goals or description."""
        result = await projects.update(project, status=status, goals=goals, description=description)
        await audit.record(
            tool="project_update",
            action="update",
            object_type="project",
            object_id=result["id"],
            previous_version=result["previous"],
        )
        return await projects.get(project)

    @mcp.tool(
        name="project_event_add",
        tags={"projects", "write"},
        auth=require_scopes(scopes.PROJECTS_WRITE),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def project_event_add(
        project: Annotated[str, Field(description="Project slug or name.")],
        summary: Annotated[str, Field(description="One line on what changed.")],
        event_type: Annotated[
            str, Field(description="status_change, milestone, note, blocker or update.")
        ] = "update",
        detail: Annotated[
            str | None, Field(description="Optional detail about the event, beyond its summary.")
        ] = None,
    ) -> dict:
        """Append an event to a project's log. Identical events are not repeated."""
        principal = current_principal()
        project_id = await projects.require(project)
        event_id = await projects.add_event(
            project_id,
            summary=summary,
            event_type=event_type,
            detail=detail,
            source_provider=principal.provider if principal.provider != "unknown" else None,
        )
        return {
            "added": event_id is not None,
            "event_id": str(event_id) if event_id else None,
            "reason": None if event_id else "An identical event is already recorded.",
        }
