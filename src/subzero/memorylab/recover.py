"""Apply memory triage to classified conversations and write the results.

Reads only stored rows: the latest non-degraded checkpoint event for each
conversation under the current QUESTIONS_VERSION. It never calls Jev itself;
``subzero reclassify-conversations`` does that. Running it again is safe: a
conversation whose decision already matches its newest event is skipped, and
notes are upserted by title.

What it writes (routing in subzero/memorylab/triage.py):

* ``inbox`` sessions: a draft in 00-Inbox with the scores and an excerpt.
* ``note`` sessions: nothing yet. Claude writes the real note from the full
  transcript in ``memory-lab extract``, into the bucket recorded here.
* every session: one ``memory_triage`` row with its outcome and layers, which
  ``memory-lab extract`` (structured, vault) and ``memory-lab honcho-sync``
  (honcho) work from.
* per run: a recovery report, a memory log, and the hot-layer candidates.

Sensitive conversations (anything not ``normal``) never reach the vault,
Honcho or a candidate list; they are recorded as ``log`` with reason
``sensitive_withheld`` and still feed the structured layer, which stays inside
Subzero.
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

#: Decisions written per round trip. The Supabase pooler costs about 175 ms a
#: statement, so one row at a time put 5,629 sessions past a quarter of an hour.
UPSERT_BATCH = 500

_UPSERT_SQL = """
INSERT INTO memory_triage (
    conversation_id, classification_event_id, questions_version, outcome,
    bucket, reason, gate, candidates, scores, note_id, layers, decided_at
)
SELECT r.conversation_id, r.classification_event_id, $2, r.outcome, r.bucket,
       r.reason, r.gate, r.candidates, r.scores, r.note_id, r.layers, now()
FROM jsonb_to_recordset($1::jsonb) AS r(
    conversation_id uuid, classification_event_id uuid, outcome text,
    bucket text, reason text, gate text, candidates text[], scores jsonb,
    note_id uuid, layers text[]
)
ON CONFLICT (conversation_id) DO UPDATE SET
    classification_event_id = EXCLUDED.classification_event_id,
    questions_version = EXCLUDED.questions_version,
    outcome = EXCLUDED.outcome, bucket = EXCLUDED.bucket,
    reason = EXCLUDED.reason, gate = EXCLUDED.gate,
    candidates = EXCLUDED.candidates, scores = EXCLUDED.scores,
    note_id = coalesce(EXCLUDED.note_id, memory_triage.note_id),
    layers = EXCLUDED.layers,
    decided_at = now()
"""

_EVENTS_SQL = """
SELECT DISTINCT ON (e.object_id)
       e.id AS event_id, e.object_id AS conversation_id, e.answers,
       c.provider, c.title, c.started_at, c.sensitivity::text AS sensitivity,
       p.id AS project_id, p.slug AS project_slug, p.name AS project_name,
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
    layers: Counter[str] = field(default_factory=Counter)
    notes_written: int = 0
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "questions_version": QUESTIONS_VERSION,
            "sessions_seen": self.seen,
            "skipped_unchanged": self.skipped_unchanged,
            "vault_notes_planned": self.outcomes["note"],
            "parked_in_inbox": self.outcomes["inbox"],
            "logged_only": self.outcomes["log"],
            "discarded": self.outcomes["discard"],
            "note_buckets": dict(self.buckets),
            "reasons": dict(self.reasons),
            "memory_candidates": dict(self.candidates),
            "layers": dict(self.layers),
            "inbox_drafts_written": self.notes_written,
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
        metadata,  # the pool's jsonb codec encodes dicts; never json.dumps here
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
    if decision.outcome != "inbox":
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


def _pending_row(row: Any, decision: Triage, note_id: UUID | None) -> dict[str, Any]:
    """One decision, as the JSON ``_UPSERT_SQL`` unpacks back into columns."""
    return {
        "conversation_id": str(row["conversation_id"]),
        "classification_event_id": str(row["event_id"]),
        "outcome": decision.outcome,
        "bucket": decision.bucket,
        "reason": decision.reason,
        "gate": decision.gate,
        "candidates": decision.candidates,
        "scores": decision.scores,
        "note_id": str(note_id) if note_id else None,
        "layers": decision.layers,
    }


async def _flush(services: Services, pending: dict[str, dict[str, Any]]) -> None:
    """Write a batch of decisions in one round trip and empty the buffer.

    Keyed by conversation so a batch can never ask Postgres to update the same
    row twice, and still idempotent: writing the same decisions again leaves
    the same table.
    """
    if not pending:
        return
    await services.db.execute(_UPSERT_SQL, list(pending.values()), QUESTIONS_VERSION)
    pending.clear()


def decide(row: Any) -> Triage:
    answers = row["answers"]
    if isinstance(answers, str):
        answers = json.loads(answers)
    result = ClassificationResult(answers=answers_from_json(answers or {}), classifier="jev")
    decision = triage(result)
    sensitive = (row["sensitivity"] or "normal") != "normal" or (
        row["summary_sensitivity"] or "normal"
    ) != "normal"
    if sensitive:
        layers = [layer for layer in decision.layers if layer == "structured"]
        return Triage(
            "log", decision.bucket, "sensitive_withheld", decision.gate, [], decision.scores, layers
        )
    if decision.tentative:
        # Jev was torn between interchangeable homes. Code answers what code
        # can: a session tied to a known project is project work, anything
        # else is reference material.
        bucket = "project" if row["project_slug"] else "resource"
        return Triage(
            decision.outcome,
            bucket,
            decision.reason,
            decision.gate,
            decision.candidates,
            decision.scores,
            decision.layers,
            True,
        )
    return decision


async def recover(
    services: Services, *, dry_run: bool = True, limit: int | None = None, redo: bool = False
) -> RecoveryReport:
    report = RecoveryReport(dry_run=dry_run)
    rows = await services.db.fetch(_EVENTS_SQL, QUESTIONS_VERSION)
    decided: list[tuple[Any, Triage]] = []
    pending: dict[str, dict[str, Any]] = {}
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
        for layer in decision.layers:
            report.layers[layer] += 1
        decided.append((row, decision))
        if dry_run:
            continue
        note_id = await _apply(services, row, decision)
        if note_id:
            report.notes_written += 1
        pending[str(row["conversation_id"])] = _pending_row(row, decision, note_id)
        if len(pending) >= UPSERT_BATCH:
            await _flush(services, pending)
    await _flush(services, pending)
    if not dry_run and report.seen:
        await write_run_notes(services, report, decided)
    return report


# ------------------------------------------------------------ run notes


def _row_line(row: Any, decision: Triage) -> str:
    title = (row["title"] or "Untitled").replace("|", "/")
    worth = decision.scores.get("worth_processing", "")
    return (
        f"| {_date(row['started_at'])} | {row['provider']} | {truncate_for_budget(title, 70)} "
        f"| {decision.reason.replace('_', ' ')} | {worth} | {decision.gate} "
        f"| {', '.join(decision.layers) or '-'} |"
    )


_ALL_SQL = """
SELECT t.conversation_id, t.outcome, t.bucket, t.reason, t.gate, t.candidates,
       t.scores, t.layers, c.provider, c.title, c.started_at, p.name AS project_name
FROM memory_triage t
JOIN conversations c ON c.id = t.conversation_id
LEFT JOIN projects p ON p.id = c.project_id
"""


async def all_decided(services: Services) -> list[tuple[Any, Triage]]:
    """Every stored decision, so the run notes describe the whole history and
    not only the sessions one run happened to decide."""
    out: list[tuple[Any, Triage]] = []
    for row in await services.db.fetch(_ALL_SQL):
        scores = row["scores"]
        if isinstance(scores, str):
            scores = json.loads(scores)
        out.append(
            (
                row,
                Triage(
                    row["outcome"],
                    row["bucket"],
                    row["reason"],
                    row["gate"],
                    list(row["candidates"] or []),
                    scores or {},
                    list(row["layers"] or []),
                ),
            )
        )
    return out


def totals(decided: list[tuple[Any, Triage]]) -> RecoveryReport:
    report = RecoveryReport(dry_run=False, seen=len(decided))
    for _row, d in decided:
        report.outcomes[d.outcome] += 1
        report.reasons[d.reason] += 1
        if d.outcome == "note" and d.bucket:
            report.buckets[d.bucket] += 1
        for kind in d.candidates:
            report.candidates[kind] += 1
        for layer in d.layers:
            report.layers[layer] += 1
    return report


def render_report(report: RecoveryReport, today: str, *, this_run: int | None = None) -> str:
    data = report.as_dict()
    ran = f" This run decided {this_run} of them." if this_run is not None else ""
    lines = [
        f"All {data['sessions_seen']} triaged sessions as of {today}, question set "
        f"`{QUESTIONS_VERSION}`.{ran}",
        "",
        "| Result | Sessions |",
        "| --- | --- |",
        f"| Processed | {data['sessions_seen']} |",
        f"| Vault notes (Claude writes them in `memory-lab extract`) | {data['vault_notes_planned']} |",
        f"| Parked in 00-Inbox (need a person) | {data['parked_in_inbox']} |",
        f"| Logged only | {data['logged_only']} |",
        f"| Discarded | {data['discarded']} |",
        "",
        "## Layers fed",
        "",
        "A session can feed several layers.",
        "",
        *[f"- {k}: {v}" for k, v in sorted(data["layers"].items())],
        "",
        "## Vault notes by bucket",
        "",
        *[f"- {k}: {v}" for k, v in sorted(data["note_buckets"].items())],
        "",
        "## Why",
        "",
        *[f"- {k.replace('_', ' ')}: {v}" for k, v in sorted(data["reasons"].items())],
        "",
        "See [[hot-memory-candidates]] and the run's memory log.",
    ]
    return "\n".join(lines)


def _when(row: Any) -> datetime:
    return row["started_at"] or datetime.min.replace(tzinfo=UTC)


def _entry(row: Any, decision: Triage) -> str:
    title = truncate_for_budget((row["title"] or "Untitled").strip(), 90)
    project = f" · {row['project_name']}" if row["project_name"] else ""
    worth = decision.scores.get("worth_processing", "")
    return (
        f"- {_date(row['started_at'])} · {row['provider']}{project} · {title} "
        f"(worth {worth}, `{str(row['conversation_id'])[:8]}`)"
    )


def render_candidates(decided: list[tuple[Any, Triage]], today: str) -> str:
    """The hot layer's inputs: what might earn a line in MEMORY.md, USER.md or a skill.

    Hot memory is for what is used often, so frequency decides as much as
    quality: projects ranked by how many recent sessions they had, and
    procedures and preferences ranked by worth, newest first among equals.
    """
    usable = [(r, d) for r, d in decided if d.candidates]
    recent = [r for r, d in decided if d.outcome != "discard" and r["project_name"]]
    cutoff = datetime.now(UTC).timestamp() - 90 * 86400
    active = Counter(r["project_name"] for r in recent if _when(r).timestamp() >= cutoff)

    def ranked(kind: str, limit: int) -> list[str]:
        rows = [(r, d) for r, d in usable if kind in d.candidates]
        rows.sort(
            key=lambda rd: (rd[1].scores.get("worth_processing", 0), _when(rd[0])), reverse=True
        )
        out = [_entry(r, d) for r, d in rows[:limit]]
        if len(rows) > limit:
            out.append(f"- … and {len(rows) - limit} more (`subzero memory-lab report`).")
        return out or ["- none yet"]

    lines = [
        f"Generated {today} by `subzero memory-lab recover`. Nothing here is promoted",
        "automatically: the weekly analysis (or you) moves a line into MEMORY.md,",
        "USER.md or a skill. MEMORY.md stays under 2,200 characters.",
        "",
        "## MEMORY.md · Current Projects (sessions in the last 90 days)",
        "",
        *([f"- {name}: {n}" for name, n in active.most_common(15)] or ["- none yet"]),
        "",
        "## MEMORY.md · Decisions",
        "",
        *ranked("decision", 40),
        "",
        "## USER.md · Preferences and working patterns",
        "",
        "These sessions also go to Honcho (`memory-lab honcho-sync`), which tracks how",
        "they change over time; promote only what has held across several sessions.",
        "",
        *ranked("preference", 40),
        "",
        "## Skills · Reusable procedures",
        "",
        "A procedure that shows up in several sessions is a skill; one that shows up",
        "once is a Permanent note.",
        "",
        *ranked("procedure", 60),
    ]
    return "\n".join(lines)


async def write_run_notes(
    services: Services, report: RecoveryReport, decided: list[tuple[Any, Triage]]
) -> None:
    today = datetime.now(UTC).date().isoformat()
    paths = services.vault_paths
    meta = {"tags": ["memory-lab", "type/experiment"]}
    everything = await all_decided(services)
    listed = [(r, d) for r, d in everything if d.reason != "sensitive_withheld"]

    title = f"Recovery report {today}"
    await _write_note(
        services,
        title=title,
        content=render_report(totals(everything), today, this_run=len(decided)),
        note_class="experiment",
        vault_path=paths.experiment("Analysis", title),
        metadata=meta,
    )

    title = f"Recovery log {today}"
    header = [
        "Sessions triage kept out of the vault: below the note threshold, low",
        "confidence, or a duplicate. Many still feed the structured, Honcho or hot",
        "layers (the layers column). Sensitive sessions are counted, never listed.",
        "",
        "| Date | App | Session | Why | worth | gate | layers |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    logged = sorted(
        ((r, d) for r, d in listed if d.outcome == "log"), key=lambda rd: _when(rd[0]), reverse=True
    )
    await _write_note(
        services,
        title=title,
        content="\n".join(header + [_row_line(r, d) for r, d in logged]),
        note_class="experiment",
        vault_path=paths.experiment("Memory-Logs", title),
        metadata=meta,
    )

    title = "Hot memory candidates"
    await _write_note(
        services,
        title=title,
        content=render_candidates(listed, today),
        note_class="experiment",
        vault_path=paths.experiment("Analysis", title),
        metadata=meta,
    )


async def write_structured(services: Services) -> int:
    """Cloudflare-style structured memory: facts, events, instructions, tasks.

    Rendered from rows Subzero already holds (memories, decisions, open
    tasks) for sessions on the structured layer. No model call; the content
    comes from Claude in ``memory-lab extract``.
    """
    facts = await services.db.fetch(
        """
        SELECT m.kind, m.content, m.created_at FROM memories m
        WHERE m.status = 'active' AND m.sensitivity = 'normal'
          AND (m.source_conversation_id IS NULL OR m.source_conversation_id IN (
                SELECT conversation_id FROM memory_triage WHERE 'structured' = ANY(layers)))
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
        SELECT t.text, p.name FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.status = 'open' ORDER BY t.priority DESC, t.created_at DESC LIMIT 200
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
        where = f" ({row['name']})" if row["name"] else ""
        groups["Tasks"].append(f"- [ ] {truncate_for_budget(row['text'], 200)}{where}")
    body = [
        "Generated from Subzero's stored memories, decisions and open tasks.",
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
