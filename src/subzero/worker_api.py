"""HTTP surface for the local Obsidian sync worker.

Separate from MCP on purpose. The worker is our own code running on the user's
machine, not a model: it authenticates with a shared secret over an
**outbound** connection and speaks a small, fixed job protocol. Nothing here
takes a path from the caller - the worker is told which path to write, and it
validates that path again locally before touching the disk.
"""

from __future__ import annotations

import hmac
import json
from typing import Any
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from subzero.config import Settings
from subzero.db.pool import Database
from subzero.logging_setup import get_logger
from subzero.notes.service import NoteService
from subzero.obsidian.queue import SyncQueue
from subzero.obsidian.render import (
    render_artifact_note,
    render_moc,
    render_note,
)
from subzero.storage.base import ObjectStore
from subzero.wiki.service import SECTION_TITLES, moc_render_input, note_graph

logger = get_logger(__name__)


def _unauthorized() -> JSONResponse:
    # No detail: an unauthenticated caller learns nothing about this service.
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def authorize(request: Request, settings: Settings) -> bool:
    expected = settings.worker_api_key.get_secret_value()
    if not expected:
        return False
    supplied = request.headers.get("x-worker-key", "")
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def lease_jobs(
    request: Request, *, settings: Settings, db: Database, store: ObjectStore, sync_queue: SyncQueue
) -> Response:
    if not authorize(request, settings):
        return _unauthorized()
    body = _json_body(await request.body())
    worker_id = str(body.get("worker_id") or "unknown")
    requested_limit = body.get("limit")
    limit = 10 if requested_limit is None else max(0, min(int(requested_limit), 50))

    jobs = await sync_queue.lease(worker_id=worker_id, limit=limit)

    deliverable: list[dict[str, Any]] = []
    for job in jobs:
        rendered = await _render_job(job, settings=settings, db=db, store=store)
        if rendered is not None:
            deliverable.append(rendered)
            continue
        # A job we cannot render was still leased. Release it explicitly with
        # a reason, rather than leaving a row that is invisible to the worker
        # and silently blocked until its lease expires.
        await sync_queue.fail(
            job["id"],
            worker_id=worker_id,
            error="server could not render this job; its target may have been deleted",
        )
        logger.warning(
            "released an unrenderable sync job",
            extra={"job_type": job["job_type"], "vault_path": job["vault_path"]},
        )

    return JSONResponse(
        {
            "jobs": deliverable,
            # The server sets the worker's next wait rather than leaving it to
            # guess: only the server knows whether work is still arriving, and
            # nothing can push a job to the laptop.
            "poll_in_seconds": await sync_queue.poll_hint(backlog=bool(deliverable)),
        },
        status_code=200,
    )


async def _render_job(
    job: dict[str, Any], *, settings: Settings, db: Database, store: ObjectStore
) -> dict[str, Any] | None:
    """Turn a queued job into exactly the bytes the worker should write.

    Rendering happens here, not on the laptop, so the worker stays a dumb,
    auditable file writer with no knowledge of the schema.
    """
    payload = {
        "id": str(job["id"]),
        "job_type": job["job_type"],
        "vault_path": job["vault_path"],
        "expected_sha256": job["expected_sha256"],
        "attempts": job["attempts"],
    }

    try:
        if job["job_type"] == "write_note":
            row = await db.fetchrow(
                """
                SELECT n.*, p.slug AS project_slug
                FROM notes n LEFT JOIN projects p ON p.id = n.project_id
                WHERE n.id = $1
                """,
                job["object_id"],
            )
            if row is None:
                return None
            related, mocs = await note_graph(job["object_id"], db=db)
            # A project or area note carries its own next steps: the open
            # tasks render inside the generated block of the note.
            next_steps = (
                [
                    dict(t)
                    for t in await db.fetch(
                        """
                        SELECT text, due_date FROM tasks
                        WHERE project_id = $1 AND status = 'open'
                        ORDER BY priority DESC, due_date NULLS LAST, created_at
                        """,
                        row["project_id"],
                    )
                ]
                if row["project_id"]
                else []
            )
            payload["content"] = render_note(
                dict(row), related=related, mocs=mocs, next_steps=next_steps
            )
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "write_moc":
            data = await moc_render_input(job["object_id"], db=db)
            if data is None:
                return None
            payload["content"] = render_moc(
                data["moc"],
                data["sections"],
                section_titles=SECTION_TITLES,
                children=data["children"],
                parent=data["parent"],
            )
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "write_artifact_note":
            artifact = await db.fetchrow(
                """
                SELECT a.*, p.slug AS project_slug
                FROM artifacts a LEFT JOIN projects p ON p.id = a.project_id
                WHERE a.id = $1
                """,
                job["object_id"],
            )
            if artifact is None:
                return None
            files = await db.fetch(
                "SELECT * FROM artifact_files WHERE artifact_id = $1 ORDER BY relative_path",
                job["object_id"],
            )
            payload["content"] = render_artifact_note(
                dict(artifact),
                [dict(f) for f in files],
                download_hint=(
                    "Ask any connected assistant for "
                    f"`artifact_signed_download` on artifact `{artifact['slug']}` "
                    "to get short-lived links to these files."
                ),
            )
            payload["content_type"] = "text/markdown"

        elif job["job_type"] == "mirror_artifact_files":
            object_key = job["payload"].get("object_key")
            if not object_key:
                return None
            # Signed URL rather than the bytes: the worker fetches directly,
            # keeping large files out of this response.
            payload["download_url"] = await store.signed_download_url(
                object_key, expires_in=settings.signed_url_ttl_seconds
            )
            payload["expected_content_sha256"] = job["content_sha256"]
            payload["content_type"] = "application/octet-stream"

        else:
            logger.warning("unknown job type", extra={"job_type": job["job_type"]})
            return None

    except Exception:
        logger.exception("failed to render sync job", extra={"job_id": str(job["id"])})
        return None

    return payload


async def ack_job(request: Request, *, settings: Settings, sync_queue: SyncQueue) -> Response:
    if not authorize(request, settings):
        return _unauthorized()
    body = _json_body(await request.body())
    try:
        job_id = UUID(str(body["job_id"]))
    except (KeyError, ValueError):
        return JSONResponse({"error": "job_id is required"}, status_code=400)

    worker_id = str(body.get("worker_id") or "unknown")
    status = str(body.get("status") or "done")

    if status == "done":
        await sync_queue.complete(job_id, worker_id=worker_id, written_sha256=body.get("sha256"))
    elif status == "conflict":
        await sync_queue.fail(
            job_id,
            worker_id=worker_id,
            error=str(body.get("error") or "file changed on disk"),
            conflict=True,
        )
    else:
        await sync_queue.fail(
            job_id, worker_id=worker_id, error=str(body.get("error") or "unknown error")
        )
    return JSONResponse({"ok": True})


async def push_edits(
    request: Request, *, settings: Settings, notes: NoteService, sync_queue: SyncQueue
) -> Response:
    """Human edits made in the vault, pushed back up by the worker."""
    if not authorize(request, settings):
        return _unauthorized()
    body = _json_body(await request.body())
    worker_id = str(body.get("worker_id") or "unknown")

    applied: list[dict[str, Any]] = []
    for edit in body.get("edits") or []:
        kind = edit.get("kind")
        try:
            if kind == "note":
                result = await notes.apply_vault_edit(
                    vault_path=edit["vault_path"],
                    content=edit["content"],
                    file_sha256=str(edit.get("sha256") or "") or None,
                )
                applied.append({"vault_path": edit["vault_path"], **result})
            await sync_queue.record_inbound(
                vault_path=str(edit.get("vault_path")),
                sha256=str(edit.get("sha256") or ""),
                worker_id=worker_id,
                detail=f"human edit ({kind})",
            )
        except Exception as exc:
            logger.exception("failed to apply vault edit")
            applied.append({"vault_path": edit.get("vault_path"), "error": str(exc)})

    return JSONResponse({"applied": applied})
