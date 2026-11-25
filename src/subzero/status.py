"""Read-only inventory counts for dashboards and MCP resources."""

from __future__ import annotations

from typing import Any

from subzero.db.pool import Database


class StatusService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def inventory_counts(self) -> dict[str, Any]:
        """Counts only, no snippets of stored content."""
        row = await self._db.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM memories WHERE status = 'active') AS memories,
              (SELECT count(*) FROM conversations) AS conversations,
              (SELECT count(*) FROM session_summaries) AS summaries,
              (SELECT count(*) FROM projects WHERE status = 'active') AS active_projects,
              (SELECT count(*) FROM tasks WHERE status IN ('open','blocked')) AS open_tasks,
              (SELECT count(*) FROM notes) AS notes,
              (SELECT count(*) FROM artifacts) AS artifacts,
              (SELECT count(*) FROM artifacts WHERE storage_status = 'captured')
                  AS artifacts_captured,
              (SELECT count(*) FROM review_queue WHERE status = 'open') AS open_reviews,
              (SELECT count(*) FROM sync_jobs
                WHERE status IN ('queued','approved','leased')) AS pending_vault_writes
            """
        )
        return dict(row) if row else {}
