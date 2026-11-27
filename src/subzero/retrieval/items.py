"""ContextItem construction and ranking weights for retrieval."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import asyncpg

from subzero.models import ContextItem, ContextPackage, Provenance_
from subzero.util import estimate_tokens, truncate_for_budget

MAX_ITEM_TOKENS = 450
PACKAGE_OVERHEAD = 110
COMPACT_BELOW = 400
MAX_CONFLICT_REPORTS = 3
RECENCY_DAYS = 90.0

NOTHING_MATCHED = (
    "Nothing in Subzero matched. Answer from the current conversation; "
    "do not assume prior context exists."
)

PROVENANCE_WEIGHT = {
    "user_statement": 1.0,
    "system": 0.9,
    "tool_result": 0.8,
    "external_source": 0.8,
    "assistant_generated": 0.7,
    "imported_metadata": 0.45,
}
KIND_WEIGHT = {
    "decision": 1.0,
    "memory": 0.95,
    "task": 0.9,
    "note": 0.85,
    "artifact": 0.8,
    "summary": 0.8,
    "message": 0.5,
}


def now() -> datetime:
    return datetime.now(UTC)


def recency(when: datetime | None) -> float:
    if when is None:
        return 0.75
    age = max(0.0, (now() - when).total_seconds() / 86400)
    return 0.5 + 0.5 * math.exp(-age / RECENCY_DAYS)


def get(row: asyncpg.Record | dict[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def wire_tokens(model: ContextItem | ContextPackage) -> int:
    from subzero.mcp.compact import dump

    return estimate_tokens(json.dumps(dump(model)))


def cost(item: ContextItem) -> int:
    return wire_tokens(item)


def item(
    *,
    kind: str,
    row: asyncpg.Record | dict[str, Any],
    content: str,
    title: str | None = None,
    score: float | None = None,
    provenance_kind: str = "assistant_generated",
    max_tokens: int = MAX_ITEM_TOKENS,
    layer: str | None = None,
    retrieval_confidence: str | None = None,
) -> ContextItem:
    trimmed = truncate_for_budget(content.strip(), max_tokens)
    questions = [str(q) for q in (get(row, "open_questions") or []) if str(q).strip()]
    status = get(row, "status")
    effective = get(row, "effective_at")
    recorded = effective or get(row, "updated_at") or get(row, "created_at")
    ctx = ContextItem(
        type=kind,  # type: ignore[arg-type]
        id=row["id"],
        title=title,
        content=trimmed,
        score=round(score, 4) if score is not None else None,
        project=get(row, "project_slug"),
        provenance=Provenance_(
            kind=get(row, "provenance") or provenance_kind,  # type: ignore[arg-type]
            provider=get(row, "source_provider") or get(row, "provider"),
            conversation_id=get(row, "source_conversation_id") or get(row, "conversation_id"),
            recorded_at=recorded.replace(microsecond=0) if recorded else None,
            confidence=get(row, "confidence"),
        ),
        effective_at=effective,
        layer=layer,
        status="superseded" if kind == "decision" and status == "superseded" else None,
        superseded_by=get(row, "superseded_by"),
        open_questions=questions,
        reason=get(row, "reason"),
        retrieval_confidence=retrieval_confidence,  # type: ignore[arg-type]
    )
    ctx.estimated_tokens = cost(ctx)
    return ctx
