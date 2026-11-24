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

Executable jobs (``kind='command'`` and ``kind='session'``, queued by the
``agents:exec`` tools) skip every routing question: no sigil parse and no
Jev, because a command is user content that must never reach the classifier.
The server executes nothing; the runner on the user's machine claims the job
and does the running. Its ``output`` is user content too: stored as a capped
tail, never sent to a classifier, an embedder or any external call, and
returned only to the caller.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any
from uuid import UUID

from subzero import sigils
from subzero.agents.exec_paths import resolve_cwd, validate_space
from subzero.classifier.base import Classifier
from subzero.classifier.questions import DISPATCH_ROUTE_AUTO, DISPATCH_TARGETS
from subzero.config import Settings
from subzero.db.pool import Database
from subzero.errors import NotFoundError, ValidationError
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

STATUSES = ("working", "waiting", "idle", "done")
#: A session not seen for this long is shown as idle, whatever it last said.
STALE_AFTER = timedelta(minutes=30)
#: Finished sessions and jobs stay on the board this long.
BOARD_WINDOW = timedelta(hours=2)
#: A session silent this long has stopped without ending (crashed, closed,
#: machine asleep for good). The board stops showing it at the same age, so
#: a job it held is failed then rather than left 'claimed' for ever.
ABANDONED_AFTER = timedelta(days=1)

#: The tail of a runner's output that is stored: 16 KB of text. A cap in
#: characters, not bytes, so a cut never splits a UTF-8 sequence.
OUTPUT_TAIL_CHARS = 16 * 1024
#: A command or first_command is capped like a goal, and for kind='command'
#: the command also rides in the goal column, capped at the same length.
MAX_COMMAND_CHARS = 4000
#: Runner-side timeout bounds, mirrored by a CHECK in migration 0021.
TIMEOUT_MIN_S = 1
TIMEOUT_MAX_S = 1800

#: Words that make a job risky enough to plan before doing.
#: Matched at the start of a word, so "deleting", "rotated" and "--force" count.
#: Over-matching only costs a plan step; under-matching can lock you out.
_RISKY = re.compile(
    r"(?<![a-z0-9])(acl|firewall|dns|tailscale|ssh|sudo|chmod|chown|credential|secret|"
    r"token|password|api[ _-]?key|key rotation|rotat|revok|delet|remov|rm\b|drop|"
    r"truncat|wipe|purge|force|push|reset --hard|rebase|merge|migrat|deploy|release|"
    r"publish|production|prod\b|billing|payment|invoice|dns|domain|cert)",
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
    def __init__(self, db: Database, classifier: Classifier, settings: Settings) -> None:
        self._db = db
        self._classifier = classifier
        self._settings = settings

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
                taken = {
                    r["name"].lower()
                    for r in await conn.fetch(
                        """
                        SELECT name FROM agent_sessions
                         WHERE ended_at IS NULL
                           AND (lower(name) = lower($1) OR starts_with(lower(name), lower($1) || '#'))
                        """,
                        label,
                    )
                }
                if label.lower() in taken:
                    n = 2
                    while f"{label}#{n}".lower() in taken:
                        n += 1
                    label = f"{label}#{n}"
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
        deliver: bool = False,
    ) -> dict[str, Any]:
        """Heartbeat; optionally change status, task or home.

        With ``deliver`` it returns new messages and marks them read, so only
        a caller that shows them to the agent (the prompt hook) should ask.
        """
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
            messages = await self._take_messages(conn, row) if deliver else []
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
                # Ending is not finishing: `subzero done` finishes a job. A
                # session that stops first leaves it failed, visibly.
                await conn.execute(
                    """
                    UPDATE dispatch_jobs SET status = 'failed', updated_at = now(),
                           result = coalesce(result, $2)
                     WHERE id = $1 AND status = 'claimed'
                    """,
                    row["job_id"],
                    f"session ended ({_clean(summary, 200) or 'no reason given'}) before the job was marked done",
                )
                await conn.execute(
                    "UPDATE agent_sessions SET job_id = NULL WHERE id = $1", session_id
                )
        return _row(row)

    async def resolve_session(self, ref: str) -> dict[str, Any]:
        """A session by id, id prefix (6+ hex characters) or exact name.

        Never guesses: if a name or prefix matches more than one live session,
        it refuses and lists their ids.
        """
        ref = ref.strip()
        prefix = ref.lower() if re.fullmatch(r"[0-9a-f-]{6,36}", ref.lower()) else None
        rows = await self._db.fetch(
            """
            SELECT * FROM agent_sessions
             WHERE (ended_at IS NULL OR id::text = $2)
               AND (left(id::text, length($2)) = $2 OR lower(name) = lower($1)
                    OR external_id = $1)
             ORDER BY last_seen_at DESC LIMIT 5
            """,
            ref,
            prefix or "~",  # never a prefix of a uuid
        )
        if not rows:
            raise NotFoundError(
                f"No running agent session matches {ref!r}. Run `subzero agents` to list them."
            )
        if len(rows) > 1:
            ids = ", ".join(str(r["id"])[:8] for r in rows)
            raise ValidationError(f"{ref!r} matches several sessions ({ids}); use one of the ids.")
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
        space: str | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            raise ValidationError("Say what the job is.")
        if mode not in ("scout", "ship"):
            raise ValidationError("mode is scout (read, plan, report) or ship (make the change).")
        space_label = validate_space(space)
        cwd = resolve_cwd(worktree, self._settings.exec_roots) if worktree else None
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
                INSERT INTO dispatch_jobs
                    (goal, mode, home, target, routed_by, confidence, created_by,
                     space, worktree, cwd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *
                """,
                goal,
                mode,
                marks.home,
                chosen,
                routed_by,
                confidence,
                _clean(created_by, 120) or "me",
                space_label,
                _clean(worktree, 500),
                cwd,
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

    # ----------------------------------------------------------- exec jobs

    async def queue_command(
        self,
        command: str,
        *,
        worktree: str | None = None,
        space: str | None = None,
        timeout_s: int | None = 120,
        target: str | None = None,
        created_by: str = "me",
    ) -> dict[str, Any]:
        """Queue one command for the runner on the user's machine.

        Nothing executes here. The command text never reaches the classifier
        or any external call: like a runner's ``output``, it is user content.
        """
        text = (command or "").strip()
        if not text:
            raise ValidationError("Say what to run.")
        if len(text) > MAX_COMMAND_CHARS:
            raise ValidationError(f"Keep commands under {MAX_COMMAND_CHARS:,} characters.")
        return await self._queue_exec(
            kind="command",
            goal=text,
            command=text,
            first_command=None,
            worktree=worktree,
            space=space,
            timeout_s=self._checked_timeout(timeout_s),
            keep_open=False,
            target=target,
            created_by=created_by,
        )

    async def queue_session(
        self,
        *,
        worktree: str | None = None,
        space: str | None = None,
        first_command: str | None = None,
        target: str | None = None,
        created_by: str = "me",
    ) -> dict[str, Any]:
        """Queue an interactive session for the runner to open.

        ``first_command`` is stored in its own column: text the runner types
        into the session for the human to review and never executes. ``goal``
        stays a human description of the job either way.
        """
        typed = (first_command or "").strip() or None
        if typed is not None and len(typed) > MAX_COMMAND_CHARS:
            raise ValidationError(f"Keep the first command under {MAX_COMMAND_CHARS:,} characters.")
        place = (worktree or "").strip() or (space or "").strip()
        goal = (
            f"Open an interactive session in {place}." if place else "Open an interactive session."
        )
        return await self._queue_exec(
            kind="session",
            goal=goal,
            command=None,
            first_command=typed,
            worktree=worktree,
            space=space,
            timeout_s=None,
            keep_open=True,
            target=target,
            created_by=created_by,
        )

    async def _queue_exec(
        self,
        *,
        kind: str,
        goal: str,
        command: str | None,
        first_command: str | None,
        worktree: str | None,
        space: str | None,
        timeout_s: int | None,
        keep_open: bool,
        target: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        """Store an executable job.

        No routing question is asked and no sigil is parsed: a ``%Code/x`` in
        a command is a path, not a mark, and the goal never goes to Jev. An
        explicit target decides ('sigil'); otherwise any free runner ('crew')
        takes it.
        """
        space_label = validate_space(space)
        cwd = resolve_cwd(worktree, self._settings.exec_roots) if worktree else None
        chosen = (target or "").lstrip(">").strip().lower() or None
        if chosen:
            if chosen not in DISPATCH_TARGETS:
                raise ValidationError(
                    f"Unknown target {chosen!r}. Use one of: {', '.join(DISPATCH_TARGETS)}."
                )
            routed_by = "sigil"
        else:
            chosen, routed_by = "crew", "default"
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO dispatch_jobs
                    (goal, target, routed_by, created_by, kind, space, worktree, cwd,
                     command, first_command, timeout_s, keep_open)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING *
                """,
                goal,
                chosen,
                routed_by,
                _clean(created_by, 120) or "me",
                kind,
                space_label,
                _clean(worktree, 500),
                cwd,
                command,
                first_command,
                timeout_s,
                keep_open,
            )
            await self._event(
                conn,
                "dispatch",
                job_id=row["id"],
                detail={"target": chosen, "kind": kind, "goal": goal[:120], "cwd": cwd},
            )
            if chosen == "me":
                await conn.execute(
                    "INSERT INTO agent_messages (from_label, to_me, body) VALUES ($1, true, $2)",
                    "dispatch",
                    f"Job for you: {goal}",
                )
        return _row(row)

    @staticmethod
    def _checked_timeout(timeout_s: int | None) -> int | None:
        if timeout_s is None:
            return None
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int):
            raise ValidationError("timeout_s must be a whole number of seconds.")
        if not TIMEOUT_MIN_S <= timeout_s <= TIMEOUT_MAX_S:
            raise ValidationError(
                f"timeout_s must be between {TIMEOUT_MIN_S} and {TIMEOUT_MAX_S} seconds."
            )
        return timeout_s

    async def claim(self, session_id: UUID, *, targets: list[str]) -> dict[str, Any] | None:
        """The oldest queued job for any of ``targets`` (plus 'crew'), or None."""
        names = sorted({t.lower() for t in targets} | {"crew"})
        async with self._db.transaction() as conn:
            holder = await conn.fetchrow(
                "SELECT job_id FROM agent_sessions WHERE id = $1 FOR UPDATE", session_id
            )
            if holder is None:
                raise NotFoundError("No such agent session.")
            await self._fail_abandoned(conn)
            if holder["job_id"] is not None:
                raise ValidationError(
                    f"This session already holds job {str(holder['job_id'])[:8]}; "
                    "finish it with `subzero done` first."
                )
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
        self,
        job_id: UUID,
        *,
        status: str = "done",
        result: str | None = None,
        exit_code: int | None = None,
        output: str | None = None,
    ) -> dict[str, Any]:
        """Record how a job ended.

        ``exit_code`` and ``output`` come back from the runner that executed
        a kind='command' job. ``output`` is user content: it is stored as a
        tail capped at :data:`OUTPUT_TAIL_CHARS`, returned to the caller, and
        never sent to the classifier, the embedder or any external call.
        """
        if status not in ("done", "failed", "cancelled"):
            raise ValidationError("status is done, failed or cancelled.")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise ValidationError("exit_code must be a whole number.")
        tail: str | None = None
        truncated: bool | None = None
        if output is not None:
            tail = output[-OUTPUT_TAIL_CHARS:]
            truncated = len(output) > OUTPUT_TAIL_CHARS
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                """
                UPDATE dispatch_jobs
                   SET status = $2, result = coalesce($3, result),
                       exit_code = coalesce($4, exit_code),
                       output = coalesce($5, output),
                       output_truncated = coalesce($6, output_truncated),
                       updated_at = now()
                 WHERE id = $1 RETURNING *
                """,
                job_id,
                status,
                _clean(result, 4000),
                exit_code,
                tail,
                truncated,
            )
            if row is None:
                raise NotFoundError("No such job.")
            if row["claimed_by"]:
                await conn.execute(
                    "UPDATE agent_sessions SET job_id = NULL WHERE id = $1 AND job_id = $2",
                    row["claimed_by"],
                    job_id,
                )
            detail = {"exit_code": row["exit_code"]} if row["exit_code"] is not None else {}
            await self._event(conn, "job_" + status, row["claimed_by"], job_id, detail)
        return _row(row)

    # ----------------------------------------------------------- board

    async def board(self, *, after_event: int = 0) -> dict[str, Any]:
        """Everything the board draws, plus events after ``after_event``."""
        async with self._db.transaction() as conn:
            await self._fail_abandoned(conn)
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

    @classmethod
    async def _fail_abandoned(cls, conn: Any) -> None:
        """Fail, visibly, each job held by a session that stopped reporting.

        The same outcome as ending a session before its job is done, for the
        session that never got to say so.
        """
        rows = await conn.fetch(
            """
            WITH gone AS (
                SELECT id, job_id FROM agent_sessions
                 WHERE job_id IS NOT NULL AND ended_at IS NULL
                   AND last_seen_at < now() - $1::interval
                   FOR UPDATE SKIP LOCKED
            ), released AS (
                -- RETURNING would give the new, NULL job_id; gone keeps the old.
                UPDATE agent_sessions s SET job_id = NULL FROM gone WHERE s.id = gone.id
            )
            UPDATE dispatch_jobs j
               SET status = 'failed', updated_at = now(),
                   result = coalesce(j.result, $2)
              FROM gone
             WHERE j.id = gone.job_id AND j.status = 'claimed'
         RETURNING j.id AS job_id, gone.id AS session_id
            """,
            ABANDONED_AFTER,
            "session stopped reporting for a day before the job was marked done",
        )
        for row in rows:
            await cls._event(
                conn, "job_failed", row["session_id"], row["job_id"], {"why": "abandoned"}
            )

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
