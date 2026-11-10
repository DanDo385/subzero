"""Live agent sessions, the messages between them, and dispatched work.

Every coding harness that runs a subzero hook registers one row here and
heartbeats it on each prompt; that is what the board draws and what lets one
session message another. Dispatched jobs wait in a queue that runners
(firstmate on the Hetzner box, a Claude Code session, the person) claim by
target name.

Routing a job never guesses silently. A ``>target`` sigil decides outright;
otherwise Jev picks among DISPATCH_TARGETS and is only trusted above
DISPATCH_ROUTE_AUTO; anything less goes to "crew", any free agent. Jobs that
touch the network, credentials or deletion always start as "scout" (read,
plan, report), whatever mode was asked for.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any
from uuid import UUID

from subzero import sigils
from subzero.classifier.base import Classifier
from subzero.classifier.questions import DISPATCH_ROUTE_AUTO, DISPATCH_TARGETS
from subzero.db.pool import Database
from subzero.errors import NotFoundError, ValidationError
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

STATUSES = ("working", "waiting", "idle", "done")
#: A session not seen for this long is shown as idle, whatever it last said.
STALE_AFTER = timedelta(minutes=30)
#: Finished sessions and jobs stay on the board this long.
BOARD_WINDOW = timedelta(hours=2)

#: Words that make a job risky enough to plan before doing.
_RISKY = re.compile(
    r"\b(acl|acls|firewall|dns|tailscale|ssh|sudo|credential|credentials|secret|secrets|"
    r"token|tokens|password|passwords|delete|deletes|remove|rm|drop|truncate|wipe|"
    r"force[- ]push|migrate|migration|deploy|production|prod)\b",
    re.IGNORECASE,
)


def is_risky(goal: str) -> bool:
    return bool(_RISKY.search(goal))


def _clean(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _row(record: Any) -> dict[str, Any]:
    out = dict(record)
    for key, value in out.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class AgentService:
    def __init__(self, db: Database, classifier: Classifier) -> None:
        self._db = db
        self._classifier = classifier

    # ----------------------------------------------------------- sessions

    async def start(
        self,
        *,
        harness: str,
        external_id: str | None = None,
        name: str | None = None,
        machine: str | None = None,
        cwd: str | None = None,
        branch: str | None = None,
        home: str | None = None,
        task: str | None = None,
    ) -> dict[str, Any]:
        """Register (or revive) a session. One row per external id."""
        harness = _clean(harness, 40) or "unknown"
        machine = _clean(machine, 60)
        cwd = _clean(cwd, 500)
        label = _clean(name, 120) or "@".join(p for p in (harness, machine) if p) + (
            f":{PurePath(cwd).name}" if cwd else ""
        )
        async with self._db.transaction() as conn:
            existing = (
                await conn.fetchrow(
                    "SELECT id, ended_at FROM agent_sessions WHERE external_id = $1 FOR UPDATE",
                    external_id,
                )
                if external_id
                else None
            )
            if existing:
                row = await conn.fetchrow(
                    """
                    UPDATE agent_sessions
                       SET status = 'working', ended_at = NULL, last_seen_at = now(),
                           cwd = coalesce($2, cwd), branch = coalesce($3, branch),
                           home = coalesce($4, home), machine = coalesce($5, machine),
                           task = coalesce($6, task)
                     WHERE id = $1 RETURNING *
                    """,
                    existing["id"],
                    cwd,
                    _clean(branch, 200),
                    _clean(home, 120),
                    machine,
                    _clean(task, 500),
                )
                if existing["ended_at"] is not None:
                    await self._event(conn, "spawn", row["id"], detail={"revived": True})
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO agent_sessions
                        (external_id, name, harness, machine, cwd, branch, home, task)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
                    """,
                    _clean(external_id, 200),
                    label,
                    harness,
                    machine,
                    cwd,
                    _clean(branch, 200),
                    _clean(home, 120),
                    _clean(task, 500),
                )
                await self._event(
                    conn, "spawn", row["id"], detail={"name": label, "home": row["home"]}
                )
        return _row(row)

    async def beat(
        self,
        session_id: UUID,
        *,
        status: str | None = None,
        task: str | None = None,
        home: str | None = None,
    ) -> dict[str, Any]:
        """Heartbeat; optionally change status, task or home. Returns new messages."""
        if status is not None and status not in STATUSES:
            raise ValidationError(f"status must be one of {', '.join(STATUSES)}.")
        async with self._db.transaction() as conn:
            before = await conn.fetchrow(
                "SELECT * FROM agent_sessions WHERE id = $1 FOR UPDATE", session_id
            )
            if before is None:
                raise NotFoundError("No such agent session. Start one first.")
            row = await conn.fetchrow(
                """
                UPDATE agent_sessions
                   SET last_seen_at = now(),
                       -- A turn ending (idle) must not clear a lesson's your-turn sign.
                       status = CASE WHEN $2 = 'idle' AND status = 'waiting' THEN status
                                     ELSE coalesce($2, status) END,
                       task = CASE WHEN $3::text IS NULL THEN task ELSE nullif($3, '') END,
                       home = coalesce($4, home),
                       ended_at = CASE WHEN coalesce($2, status) = 'done'
                                       THEN coalesce(ended_at, now()) ELSE NULL END
                 WHERE id = $1 RETURNING *
                """,
                session_id,
                status,
                _clean(task, 500) if task is not None else None,
                _clean(home, 120),
            )
            if row["home"] != before["home"]:
                await self._event(
                    conn, "move", session_id, detail={"from": before["home"], "to": row["home"]}
                )
            if row["status"] != before["status"] or (task is not None and task != before["task"]):
                await self._event(
                    conn,
                    "status",
                    session_id,
                    detail={"status": row["status"], "task": row["task"]},
                )
            messages = await self._take_messages(conn, row)
        return {"session": _row(row), "messages": messages}

    async def end(self, session_id: UUID, *, summary: str | None = None) -> dict[str, Any]:
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                UPDATE agent_sessions
                   SET status = 'done', ended_at = coalesce(ended_at, now()), last_seen_at = now()
                 WHERE id = $1 RETURNING *
                """,
                session_id,
            )
            if row is None:
                raise NotFoundError("No such agent session.")
            await self._event(conn, "end", session_id, detail={"summary": _clean(summary, 500)})
            if row["job_id"]:
                await conn.execute(
                    """
                    UPDATE dispatch_jobs SET status = 'done', updated_at = now(),
                           result = coalesce(result, $2)
                     WHERE id = $1 AND status = 'claimed'
                    """,
                    row["job_id"],
                    _clean(summary, 4000),
                )
        return _row(row)

    async def resolve_session(self, ref: str) -> dict[str, Any]:
        """A session by id, id prefix (6+ characters) or name, newest first."""
        ref = ref.strip()
        rows = await self._db.fetch(
            """
            SELECT * FROM agent_sessions
             WHERE id::text = $1 OR (length($1) >= 6 AND id::text LIKE $1 || '%')
                OR lower(name) = lower($1) OR external_id = $1
             ORDER BY (ended_at IS NULL) DESC, last_seen_at DESC LIMIT 2
            """,
            ref,
        )
        if not rows:
            raise NotFoundError(
                f"No agent session matches {ref!r}. Run `subzero agents` to list them."
            )
        return _row(rows[0])

    # ----------------------------------------------------------- messages

    async def send(
        self,
        *,
        to: str,
        body: str,
        from_session: UUID | None = None,
        from_label: str = "me",
    ) -> dict[str, Any]:
        """Send to 'me', to a home (^lesson, %project, &setup) or to one session."""
        body = (body or "").strip()
        if not body:
            raise ValidationError("A message needs some text.")
        if len(body) > 4000:
            raise ValidationError("Keep messages under 4,000 characters.")
        target = to.strip()
        to_session = to_home = None
        to_me = False
        if target.lower() in ("me", "@me", "you"):
            to_me = True
        elif target[:1] in "%^&":
            marks = sigils.parse(target)
            if not marks.home:
                raise ValidationError(f"{target!r} is not a home. Try ^cfa2, %Code/site or &herdr.")
            to_home = marks.home
        else:
            to_session = UUID((await self.resolve_session(target))["id"])
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO agent_messages
                    (from_session, from_label, to_session, to_home, to_me, body)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
                """,
                from_session,
                _clean(from_label, 120) or "me",
                to_session,
                to_home,
                to_me,
                body,
            )
            await self._event(
                conn,
                "message",
                from_session,
                detail={
                    "to": "me" if to_me else to_home or str(to_session),
                    "to_session": str(to_session) if to_session else None,
                    "preview": body[:80],
                },
            )
        return _row(row)

    async def _take_messages(self, conn: Any, session: Any) -> list[dict[str, Any]]:
        """Unread messages for a session (direct or to its home); marks them read."""
        rows = await conn.fetch(
            """
            SELECT m.* FROM agent_messages m
             WHERE (m.to_session = $1 AND m.read_at IS NULL)
                OR (m.to_home IS NOT NULL AND m.to_home = $2
                    AND m.from_session IS DISTINCT FROM $1
                    AND m.created_at >= $3
                    AND NOT EXISTS (SELECT 1 FROM agent_message_reads r
                                     WHERE r.message_id = m.id AND r.session_id = $1))
             ORDER BY m.created_at
             LIMIT 20
            """,
            session["id"],
            session["home"],
            session["started_at"],
        )
        if rows:
            await conn.execute(
                "UPDATE agent_messages SET read_at = now() WHERE id = ANY($1::uuid[]) AND to_session = $2",
                [r["id"] for r in rows],
                session["id"],
            )
            await conn.executemany(
                "INSERT INTO agent_message_reads (message_id, session_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                [(r["id"], session["id"]) for r in rows if r["to_home"]],
            )
        return [_row(r) for r in rows]

    async def messages_for_me(self, *, mark_read: bool = True) -> list[dict[str, Any]]:
        async with self._db.transaction() as conn:
            rows = await conn.fetch(
                "SELECT * FROM agent_messages WHERE to_me AND read_at IS NULL ORDER BY created_at LIMIT 50"
            )
            if mark_read and rows:
                await conn.execute(
                    "UPDATE agent_messages SET read_at = now() WHERE id = ANY($1::uuid[])",
                    [r["id"] for r in rows],
                )
        return [_row(r) for r in rows]

    # ----------------------------------------------------------- dispatch

    async def dispatch(
        self,
        goal: str,
        *,
        mode: str = "scout",
        created_by: str = "me",
        target: str | None = None,
    ) -> dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            raise ValidationError("Say what the job is.")
        if mode not in ("scout", "ship"):
            raise ValidationError("mode is scout (read, plan, report) or ship (make the change).")
        marks = sigils.parse(goal)
        notes: list[str] = []
        if mode == "ship" and is_risky(goal):
            mode = "scout"
            notes.append(
                "Started as scout: it touches the network, credentials, deletion or "
                "deployment. Approve the plan, then dispatch again with ship."
            )
        chosen = (target or "").lstrip(">").strip().lower() or (
            marks.targets[0] if marks.targets else None
        )
        confidence: float | None = None
        if chosen:
            if chosen not in DISPATCH_TARGETS:
                raise ValidationError(
                    f"Unknown target {chosen!r}. Use one of: {', '.join(DISPATCH_TARGETS)}."
                )
            routed_by = "sigil"
        else:
            result = await self._classifier.classify_dispatch(
                {"goal": goal, "home": marks.home}, DISPATCH_TARGETS
            )
            answer = result.answers.get("target")
            picked = str(answer.value) if answer else "crew"
            confidence = answer.confidence if answer else None
            if (
                not result.degraded
                and picked in DISPATCH_TARGETS
                and (confidence or 0) >= DISPATCH_ROUTE_AUTO
            ):
                chosen, routed_by = picked, "jev"
            else:
                chosen, routed_by = "crew", "default"
                if result.degraded:
                    notes.append("Jev was unavailable, so it went to any free agent.")
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO dispatch_jobs (goal, mode, home, target, routed_by, confidence, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *
                """,
                goal,
                mode,
                marks.home,
                chosen,
                routed_by,
                confidence,
                _clean(created_by, 120) or "me",
            )
            await self._event(
                conn,
                "dispatch",
                job_id=row["id"],
                detail={"target": chosen, "home": marks.home, "goal": goal[:120], "mode": mode},
            )
            if chosen == "me":
                await conn.execute(
                    "INSERT INTO agent_messages (from_label, to_me, body) VALUES ($1, true, $2)",
                    "dispatch",
                    f"Job for you: {goal}",
                )
        out = _row(row)
        out["notes"] = notes
        return out

    async def claim(self, session_id: UUID, *, targets: list[str]) -> dict[str, Any] | None:
        """The oldest queued job for any of ``targets`` (plus 'crew'), or None."""
        names = sorted({t.lower() for t in targets} | {"crew"})
        async with self._db.transaction() as conn:
            job = await conn.fetchrow(
                """
                SELECT * FROM dispatch_jobs
                 WHERE status = 'queued' AND target = ANY($1::text[])
                 ORDER BY (target = 'crew'), created_at
                 LIMIT 1 FOR UPDATE SKIP LOCKED
                """,
                names,
            )
            if job is None:
                return None
            job = await conn.fetchrow(
                """
                UPDATE dispatch_jobs SET status = 'claimed', claimed_by = $2, updated_at = now()
                 WHERE id = $1 RETURNING *
                """,
                job["id"],
                session_id,
            )
            session = await conn.fetchrow(
                """
                UPDATE agent_sessions
                   SET job_id = $2, task = $3, status = 'working', last_seen_at = now(),
                       home = coalesce($4, home)
                 WHERE id = $1 RETURNING *
                """,
                session_id,
                job["id"],
                job["goal"][:500],
                job["home"],
            )
            if session is None:
                raise NotFoundError("No such agent session.")
            await self._event(conn, "claim", session_id, job["id"], {"home": job["home"]})
        return _row(job)

    async def finish(
        self, job_id: UUID, *, status: str = "done", result: str | None = None
    ) -> dict[str, Any]:
        if status not in ("done", "failed", "cancelled"):
            raise ValidationError("status is done, failed or cancelled.")
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                UPDATE dispatch_jobs SET status = $2, result = coalesce($3, result), updated_at = now()
                 WHERE id = $1 RETURNING *
                """,
                job_id,
                status,
                _clean(result, 4000),
            )
            if row is None:
                raise NotFoundError("No such job.")
            if row["claimed_by"]:
                await conn.execute(
                    "UPDATE agent_sessions SET job_id = NULL WHERE id = $1 AND job_id = $2",
                    row["claimed_by"],
                    job_id,
                )
            await self._event(conn, "job_" + status, row["claimed_by"], job_id, {})
        return _row(row)

    # ----------------------------------------------------------- board

    async def board(self, *, after_event: int = 0) -> dict[str, Any]:
        """Everything the board draws, plus events after ``after_event``."""
        now = datetime.now(UTC)
        sessions = await self._db.fetch(
            """
            SELECT * FROM agent_sessions
             WHERE ended_at IS NULL AND last_seen_at > now() - interval '1 day'
                OR ended_at > now() - $1::interval
             ORDER BY started_at
            """,
            BOARD_WINDOW,
        )
        jobs = await self._db.fetch(
            """
            SELECT * FROM dispatch_jobs
             WHERE status IN ('queued', 'claimed') OR updated_at > now() - $1::interval
             ORDER BY created_at DESC LIMIT 50
            """,
            BOARD_WINDOW,
        )
        events = await self._db.fetch(
            "SELECT * FROM agent_events WHERE id > $1 ORDER BY id DESC LIMIT 200", after_event
        )
        vault = await self._db.fetch(
            """
            SELECT id, vault_path, status, updated_at FROM sync_jobs
             ORDER BY updated_at DESC LIMIT 20
            """
        )
        inbox = await self._db.fetch(
            "SELECT * FROM agent_messages WHERE to_me AND read_at IS NULL ORDER BY created_at DESC LIMIT 20"
        )
        shown = []
        for record in sessions:
            item = _row(record)
            if (
                item["status"] in ("working", "waiting")
                and now - record["last_seen_at"] > STALE_AFTER
            ):
                item["status"] = "idle"
            shown.append(item)
        return {
            "now": now.isoformat(),
            "sessions": shown,
            "jobs": [_row(j) for j in jobs],
            "events": [_row(e) for e in reversed(events)],
            "vault": [_row(v) for v in vault],
            "inbox": [_row(m) for m in inbox],
            "targets": list(DISPATCH_TARGETS),
        }

    # ----------------------------------------------------------- internals

    @staticmethod
    async def _event(
        conn: Any,
        kind: str,
        session_id: Any = None,
        job_id: Any = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            "INSERT INTO agent_events (kind, session_id, job_id, detail) VALUES ($1, $2, $3, $4)",
            kind,
            session_id,
            job_id,
            detail or {},
        )
