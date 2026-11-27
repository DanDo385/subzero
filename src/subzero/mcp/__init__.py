"""MCP tool, prompt and resource registration."""

from __future__ import annotations

from fastmcp import FastMCP

from subzero.mcp import (
    prompts,
    tools_agents,
    tools_artifacts,
    tools_cli,
    tools_context,
    tools_email,
    tools_inbox,
    tools_memory,
    tools_projects,
    tools_review,
)
from subzero.services import Services


def register_all(mcp: FastMCP, s: Services) -> None:
    """The one place that reads the container: each module gets only its own."""
    tools_context.register(mcp, retrieval=s.retrieval, settings=s.settings)
    tools_email.register(mcp, emails=s.email)
    tools_memory.register(
        mcp, audit=s.audit, memories=s.memories, projects=s.projects, sessions=s.sessions
    )
    tools_inbox.register(
        mcp,
        audit=s.audit,
        classification=s.classification,
        db=s.db,
        notes=s.notes,
        projects=s.projects,
        settings=s.settings,
        sync_queue=s.sync_queue,
        vault_paths=s.vault_paths,
    )
    tools_projects.register(mcp, audit=s.audit, projects=s.projects)
    tools_artifacts.register(
        mcp,
        artifacts=s.artifacts,
        audit=s.audit,
        db=s.db,
        projects=s.projects,
        settings=s.settings,
        store=s.store,
        filing=s.filing,
    )
    tools_review.register(
        mcp,
        artifacts=s.artifacts,
        audit=s.audit,
        db=s.db,
        projects=s.projects,
        review=s.review,
        settings=s.settings,
        sync_queue=s.sync_queue,
        vault_paths=s.vault_paths,
    )
    tools_agents.register(mcp, agents=s.agents, audit=s.audit)
    tools_cli.register(mcp, audit=s.audit, settings=s.settings)
    prompts.register(mcp, inventory=s.status)


__all__ = ["register_all"]
