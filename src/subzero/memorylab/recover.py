"""Apply memory triage to classified conversations and write the results.

Reads only stored rows: the latest non-degraded checkpoint event for each
conversation under the current QUESTIONS_VERSION. It never calls Jev itself;
``subzero reclassify-conversations`` does that. Running it again is safe: a
conversation whose decision already matches its newest event is skipped, and
notes are upserted by title.

Outcomes (subzero/memorylab/triage.py):

* ``note``: a note in the PARA bucket (or Permanent) with the Jev scores.
* ``inbox``: a draft in 00-Inbox with the scores and an excerpt.
* ``log``: a row in the run's memory log, no note.
* ``discard``: counted only.

Sensitive conversations (anything not ``normal``) never get a vault note; they
are recorded as ``log`` with reason ``sensitive_withheld``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from subzero.classifier.base import ClassificationResult, answers_from_json
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.logging_setup import get_logger
from subzero.memorylab.triage import Triage, has_triage, triage
from subzero.services import Services
from subzero.util import slugify, truncate_for_budget

logger = get_logger(__name__)

_EVENTS_SQL = """
SELECT DISTINCT ON (e.object_id)
       e.id AS event_id, e.object_id AS conversation_id, e.answers,
       c.provider, c.title, c.started_at, c.sensitivity::text AS sensitivity,
       p.id AS project_id, p.slug AS project_slug,
       s.summary, s.sensitivity::text AS summary_sensitivity,
       t.classification_event_id AS decided_event
FROM classification_events e
JOIN conversations c ON c.id = e.object_id
LEFT JOIN projects p ON p.id = c.project_id
LEFT JOIN session_summaries s ON s.conversation_id = c.id
LEFT JOIN memory_triage t ON t.conversation_id = c.id
WHERE e.object_type = 'checkpoint'
  AND e.questions_version = $1
  AND NOT e.degraded
ORDER BY e.object_id, e.created_at DESC
"""


@dataclass
class RecoveryReport:
    seen: int = 0
    skipped_unchanged: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    reasons: Counter[str] = field(default_factory=Counter)
    buckets: Counter[str] = field(default_factory=Counter)
    candidates: Counter[str] = field(default_factory=Counter)
    notes_written: int = 0
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "questions_version": QUESTIONS_VERSION,
            "sessions_seen": self.seen,
            "skipped_unchanged": self.skipped_unchanged,
            "notes_created_directly": self.outcomes["note"],
            "parked_in_inbox": self.outcomes["inbox"],
            "logged_only": self.outcomes["log"],
            "discarded": self.outcomes["discard"],
            "note_buckets": dict(self.buckets),
            "reasons": dict(self.reasons),
            "memory_candidates": dict(self.candidates),
            "notes_written": self.notes_written,
        }


def _date(value: datetime | None) -> str:
    return value.date().isoformat() if value else "undated"


def _title(row: Any) -> str:
    base = (row["title"] or "Untitled session").strip()
    return f"{truncate_for_budget(base, 80)} ({_date(row['started_at'])})"


def _scores_block(decision: Triage) -> list[str]:
    lines = ["## Jev triage", "", "| Question | Answer |", "| --- | --- |"]
    for key, value in decision.scores.items():
        if key.endswith("_confidence"):
            continue
        conf = decision.scores.get(f"{key}_confidence")
        shown = f"{value} ({conf})" if conf is not None else str(value)
        lines.append(f"| {key} | {shown} |")
    lines += [
        "",
        f"Outcome **{decision.outcome}** ({decision.reason.replace('_', ' ')}), "
        f"confidence gate **{decision.gate}**.",
    ]
    if decision.candidates:
        lines.append(f"Hot-memory candidate: {', '.join(decision.candidates)}.")
    return lines


def render_session_note(row: Any, decision: Triage) -> str:
    summary = (row["summary"] or "").strip() or "No summary was stored for this session."
    parts = ["## Summary", "", truncate_for_budget(summary, 1500), ""]
    if decision.outcome == "inbox":
        parts = [
            "> Draft parked by Jev triage. Keep, move, merge or delete it; the weekly",
            "> analysis is the only automatic way out of the Inbox.",
            "",
            *parts,
        ]
        if decision.bucket:
            parts += [f"Suggested home: **{decision.bucket}**.", ""]
    parts += _scores_block(decision)
    parts += [
        "",
        f"Source: {row['provider']} session `{row['conversation_id']}` "
        f"from {_date(row['started_at'])}.",
    ]
    return "\n".join(parts)


async def _write_note(
    services: Services,
    *,
    title: str,
    content: str,
    note_class: str,
    vault_path: str,
    metadata: dict[str, Any],
    project_id: UUID | None = None,
    source_provider: str | None = None,
    source_conversation_id: UUID | None = None,
) -> UUID:
    note_id, _created, _version = await services.notes.upsert(
        title=title,
        content=content,
        note_class=note_class,
        project_id=project_id,
        vault_path=vault_path,
        source_provider=source_provider,
        source_conversation_id=source_conversation_id,
        provenance="system",
        author="system",
    )
    await services.db.execute(
        """
        UPDATE notes
        SET note_class = $2, vault_path = $3, metadata = metadata || $4::jsonb
        WHERE id = $1
        """,
        note_id,
        note_class,
        vault_path,
        json.dumps(metadata),
    )
    if services.settings.obsidian_enabled:
        await services.sync_queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_id,
            vault_path=vault_path,
            payload={"note_id": str(note_id), "reason": "memory-lab"},
        )
    return note_id


async def _unique_title(services: Services, title: str, conversation_id: UUID) -> str:
    owner = await services.db.fetchval(
        "SELECT source_conversation_id FROM notes WHERE slug = $1", slugify(title, fallback="note")
    )
    if owner is None or owner == conversation_id:
        return title
    return f"{title} {str(conversation_id)[:6]}"


async def _apply(services: Services, row: Any, decision: Triage) -> UUID | None:
    if decision.outcome not in ("note", "inbox"):
        return None
    title = await _unique_title(services, _title(row), row["conversation_id"])
    note_class = decision.note_class
    path = services.vault_paths.note(
        note_class=note_class,
        title=title,
        project_slug=row["project_slug"] if note_class == "project" else None,
    )
    return await _write_note(
        services,
        title=title,
        content=render_session_note(row, decision),
        note_class=note_class,
        vault_path=path,
        metadata={
            "jev": decision.scores,
            "triage": {
                "outcome": decision.outcome,
                "reason": decision.reason,
                "gate": decision.gate,
                "candidates": decision.candidates,
                "questions_version": QUESTIONS_VERSION,
            },
            "source_date": _date(row["started_at"]),
            "tags": ["memory-lab", f"triage/{decision.outcome}"],
        },
        project_id=row["project_id"],
        source_provider=row["provider"],
        source_conversation_id=row["conversation_id"],
    )


def decide(row: Any) -> Triage:
    answers = row["answers"]
    if isinstance(answers, str):
        answers = json.loads(answers)
    result = ClassificationResult(answers=answers_from_json(answers or {}), classifier="jev")
    decision = triage(result)
    sensitive = (row["sensitivity"] or "normal") != "normal" or (
        row["summary_sensitivity"] or "normal"
    ) != "normal"
    if sensitive and decision.outcome in ("note", "inbox"):
        return Triage(
            "log", decision.bucket, "sensitive_withheld", decision.gate, [], decision.scores
        )
    if decision.outcome == "note" and decision.bucket == "project" and not row["project_slug"]:
        # Subzero never creates a project from a guess: park it with the hint.
        return Triage(
            "inbox",
            "project",
            "no_matching_project",
            decision.gate,
            decision.candidates,
            decision.scores,
        )
    return decision


async def recover(
    services: Services, *, dry_run: bool = True, limit: int | None = None, redo: bool = False
) -> RecoveryReport:
    report = RecoveryReport(dry_run=dry_run)
    rows = await services.db.fetch(_EVENTS_SQL, QUESTIONS_VERSION)
    logged: list[tuple[Any, Triage]] = []
    candidates: list[tuple[Any, Triage]] = []
    for row in rows:
        if limit is not None and report.seen >= limit:
            break
        answers = row["answers"]
        if isinstance(answers, str):
            answers = json.loads(answers)
        if not has_triage(ClassificationResult(answers_from_json(answers or {}), "jev")):
            continue
        if not redo and row["decided_event"] == row["event_id"]:
            report.skipped_unchanged += 1
            continue
        report.seen += 1
        decision = decide(row)
        report.outcomes[decision.outcome] += 1
        report.reasons[decision.reason] += 1
        if decision.outcome == "note" and decision.bucket:
            report.buckets[decision.bucket] += 1
        for kind in decision.candidates:
            report.candidates[kind] += 1
        if decision.outcome == "log" and decision.reason != "sensitive_withheld":
            logged.append((row, decision))
        if decision.candidates and decision.reason != "sensitive_withheld":
            candidates.append((row, decision))
        if dry_run:
            continue
        note_id = await _apply(services, row, decision)
        if note_id:
            report.notes_written += 1
        await services.db.execute(
            """
            INSERT INTO memory_triage (
                conversation_id, classification_event_id, questions_version, outcome,
                bucket, reason, gate, candidates, scores, note_id, decided_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10, now())
            ON CONFLICT (conversation_id) DO UPDATE SET
                classification_event_id = EXCLUDED.classification_event_id,
                questions_version = EXCLUDED.questions_version,
                outcome = EXCLUDED.outcome, bucket = EXCLUDED.bucket,
                reason = EXCLUDED.reason, gate = EXCLUDED.gate,
                candidates = EXCLUDED.candidates, scores = EXCLUDED.scores,
                note_id = coalesce(EXCLUDED.note_id, memory_triage.note_id),
                decided_at = now()
            """,
            row["conversation_id"],
            row["event_id"],
            QUESTIONS_VERSION,
            decision.outcome,
            decision.bucket,
            decision.reason,
            decision.gate,
            decision.candidates,
            json.dumps(decision.scores),
            note_id,
        )
    if not dry_run and report.seen:
        await write_run_notes(services, report, logged, candidates)
    return report


# ------------------------------------------------------------ run notes


def _row_line(row: Any, decision: Triage) -> str:
    title = (row["title"] or "Untitled").replace("|", "/")
    worth = decision.scores.get("worth_processing", "")
    return (
        f"| {_date(row['started_at'])} | {row['provider']} | {truncate_for_budget(title, 70)} "
        f"| {decision.reason.replace('_', ' ')} | {worth} | {decision.gate} |"
    )


def render_report(report: RecoveryReport, today: str) -> str:
    data = report.as_dict()
    lines = [
        f"Run on {today} with question set `{QUESTIONS_VERSION}`.",
        "",
        "| Result | Sessions |",
        "| --- | --- |",
        f"| Processed | {data['sessions_seen']} |",
        f"| Notes created directly | {data['notes_created_directly']} |",
        f"| Parked in 00-Inbox | {data['parked_in_inbox']} |",
        f"| Logged only | {data['logged_only']} |",
        f"| Discarded | {data['discarded']} |",
        "",
        "## Direct notes by bucket",
        "",
        *[f"- {k}: {v}" for k, v in sorted(data["note_buckets"].items())],
        "",
        "## Why",
        "",
        *[f"- {k.replace('_', ' ')}: {v}" for k, v in sorted(data["reasons"].items())],
        "",
        "## Hot-memory candidates",
        "",
        *[f"- {k}: {v}" for k, v in sorted(data["memory_candidates"].items())],
        "",
        "See [[memory-candidates]] and the run's memory log.",
    ]
    return "\n".join(lines)


async def write_run_notes(
    services: Services,
    report: RecoveryReport,
    logged: list[tuple[Any, Triage]],
    candidates: list[tuple[Any, Triage]],
) -> None:
    today = datetime.now(UTC).date().isoformat()
    paths = services.vault_paths
    meta = {"tags": ["memory-lab", "type/experiment"]}

    title = f"Recovery report {today}"
    await _write_note(
        services,
        title=title,
        content=render_report(report, today),
        note_class="experiment",
        vault_path=paths.experiment("Analysis", title),
        metadata=meta,
    )

    title = f"Recovery log {today}"
    header = [
        "Sessions Jev triage logged without a note: below the worth threshold, low",
        "confidence, or a duplicate. Sensitive sessions are counted, never listed.",
        "",
        "| Date | App | Session | Why | worth | gate |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    logged.sort(
        key=lambda item: item[0]["started_at"] or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    await _write_note(
        services,
        title=title,
        content="\n".join(header + [_row_line(r, d) for r, d in logged]),
        note_class="experiment",
        vault_path=paths.experiment("Memory-Logs", title),
        metadata=meta,
    )

    title = "Memory candidates"
    lines = [
        "Sessions Jev marked as carrying a stable preference, a reusable procedure or",
        "a decision. Promote by hand (or in the weekly analysis) into MEMORY.md,",
        "USER.md, a skill or a Permanent note; Jev never edits those files.",
        "",
    ]
    for kind in ("preference", "procedure", "decision"):
        rows = [(r, d) for r, d in candidates if kind in d.candidates]
        lines += [f"## {kind.title()} ({len(rows)})", ""]
        lines += [
            f"- {_date(r['started_at'])} · {r['provider']} · "
            f"{truncate_for_budget((r['title'] or 'Untitled').strip(), 90)} "
            f"(`{str(r['conversation_id'])[:8]}`)"
            for r, _d in rows[:200]
        ]
        if len(rows) > 200:
            lines.append(f"- … and {len(rows) - 200} more (query memory_triage).")
        lines.append("")
    await _write_note(
        services,
        title=title,
        content="\n".join(lines),
        note_class="experiment",
        vault_path=paths.experiment("Analysis", title),
        metadata=meta,
    )


async def write_structured(services: Services) -> int:
    """Cloudflare-style structured memory: facts, events, instructions, tasks.

    Rendered from rows Subzero already holds (memories, decisions, open list
    items) for conversations triage kept. No model call.
    """
    facts = await services.db.fetch(
        """
        SELECT m.kind, m.content, m.created_at FROM memories m
        WHERE m.status = 'active' AND m.sensitivity = 'normal'
          AND (m.source_conversation_id IS NULL OR m.source_conversation_id IN (
                SELECT conversation_id FROM memory_triage WHERE outcome IN ('note','inbox')))
        ORDER BY m.importance DESC, m.created_at DESC LIMIT 400
        """
    )
    decisions = await services.db.fetch(
        """
        SELECT statement, decided_at FROM decisions
        WHERE status = 'active' ORDER BY decided_at DESC LIMIT 200
        """
    )
    tasks = await services.db.fetch(
        """
        SELECT i.text, l.name FROM list_items i JOIN lists l ON l.id = i.list_id
        WHERE i.status = 'open' ORDER BY i.priority DESC, i.created_at DESC LIMIT 200
        """
    )
    groups: dict[str, list[str]] = {"Facts": [], "Instructions": [], "Events": [], "Tasks": []}
    for row in facts:
        target = "Instructions" if row["kind"] == "preference" else "Facts"
        groups[target].append(f"- {truncate_for_budget(row['content'], 240)}")
    for row in decisions:
        groups["Events"].append(
            f"- {row['decided_at'].date().isoformat()}: {truncate_for_budget(row['statement'], 240)}"
        )
    for row in tasks:
        groups["Tasks"].append(f"- [ ] {truncate_for_budget(row['text'], 200)} ({row['name']})")
    body = [
        "Generated from Subzero's stored memories, decisions and open list items.",
        "Facts are what is true, instructions how to work, events what was decided",
        "and when, tasks what is still open. Regenerate with `subzero memory-lab structured`.",
        "",
    ]
    for heading, items in groups.items():
        body += [f"## {heading} ({len(items)})", "", *items, ""]
    title = "Structured memories"
    await _write_note(
        services,
        title=title,
        content="\n".join(body),
        note_class="experiment",
        vault_path=services.vault_paths.experiment("Structured", title),
        metadata={"tags": ["memory-lab", "type/experiment"]},
    )
    return sum(len(items) for items in groups.values())
