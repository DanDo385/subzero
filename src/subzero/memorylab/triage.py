"""Turn Jev's triage answers into one outcome per session.

The decision policy, in order (first match wins):

1. Degraded result (Jev was not reached): ``log``. Nothing is drafted from a
   guess; the conversation is reclassified later.
2. ``worth_processing`` below TRIAGE_WORTH_MIN, or ``para_bucket=discard``:
   ``discard`` when the gate is high (Jev is sure it is worthless), ``log``
   otherwise.
3. Low ``confidence_gate``: ``log``.
4. High gate, a clear bucket (not ``inbox``), relation not ``unclear`` and not
   ``duplicates``: ``note`` in that bucket, or in ``permanent`` when Jev rated
   it ready to stand alone.
5. Anything else that is worth processing: ``inbox`` (a draft in 00-Inbox).

Independently, in a session worth processing, a confident preference, procedure or decision marks the
session as a hot-memory candidate (MEMORY.md / USER.md). Being a candidate
never promotes anything by itself; the weekly analysis does that.

Every threshold lives in classifier/questions.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from subzero.classifier.base import ClassificationResult
from subzero.classifier.questions import (
    TRIAGE_ATOMICITY_LEVELS,
    TRIAGE_CANDIDATE_MIN,
    TRIAGE_CONFIDENCE_LEVELS,
    TRIAGE_GATE_HIGH,
    TRIAGE_GATE_MEDIUM,
    TRIAGE_WORTH_MIN,
)

OUTCOMES = ("note", "inbox", "log", "discard")

#: PARA bucket -> note class. Classes resolve to vault roots in VaultPaths.
BUCKET_NOTE_CLASS: dict[str, str] = {
    "project": "project",
    "area": "area",
    "resource": "resource",
    "archive": "archive",
    "permanent": "permanent",
}

_TRIAGE_KEYS = (
    "worth_processing",
    "primary_intent",
    "has_decision",
    "has_preference",
    "has_procedure",
    "atomicity",
    "para_bucket",
    "relation_to_existing",
    "confidence_gate",
)


@dataclass(frozen=True)
class Triage:
    outcome: str
    bucket: str | None
    reason: str
    gate: str
    candidates: list[str] = field(default_factory=list)
    scores: dict[str, Any] = field(default_factory=dict)

    @property
    def note_class(self) -> str:
        if self.outcome == "note" and self.bucket:
            return BUCKET_NOTE_CLASS[self.bucket]
        return "inbox"


def _level_name(levels: list[str], index: float) -> str:
    return levels[max(0, min(len(levels) - 1, round(index)))].split(".", 1)[0].lower()


def has_triage(result: ClassificationResult) -> bool:
    return "worth_processing" in result.answers and "confidence_gate" in result.answers


def scores_of(result: ClassificationResult) -> dict[str, Any]:
    """The triage answers in a compact, frontmatter-friendly form."""
    out: dict[str, Any] = {}
    for key in _TRIAGE_KEYS:
        answer = result.answers.get(key)
        if answer is None:
            continue
        if answer.kind == "noul":
            out[key] = round(float(answer.value), 2)
        elif answer.kind == "choice":
            out[key] = str(answer.value)
            out[f"{key}_confidence"] = round(float(answer.confidence or 0.0), 2)
        else:
            out[key] = round(float(answer.value), 2)
    return out


def triage(result: ClassificationResult) -> Triage:
    scores = scores_of(result)
    worth = result.noul("worth_processing", 0.0)
    gate_raw = float(result.answers["confidence_gate"].value) if has_triage(result) else 0.0
    gate = (
        "high"
        if gate_raw >= TRIAGE_GATE_HIGH
        else "medium"
        if gate_raw >= TRIAGE_GATE_MEDIUM
        else "low"
    )
    bucket, _ = result.choice("para_bucket")
    relation, _ = result.choice("relation_to_existing")
    atomicity = result.answers.get("atomicity")
    ready = atomicity is not None and _level_name(
        TRIAGE_ATOMICITY_LEVELS, float(atomicity.value)
    ).startswith("ready")

    candidates = [
        kind
        for kind, key in (
            ("preference", "has_preference"),
            ("procedure", "has_procedure"),
            ("decision", "has_decision"),
        )
        if result.noul(key, 0.0) >= TRIAGE_CANDIDATE_MIN
    ]

    # Only something worth processing can be a hot-memory candidate.
    keep_candidates = worth >= TRIAGE_WORTH_MIN and bucket != "discard" and not result.degraded

    def make(outcome: str, reason: str, chosen: str | None = None) -> Triage:
        return Triage(outcome, chosen, reason, gate, candidates if keep_candidates else [], scores)

    if result.degraded or not has_triage(result):
        return make("log", "classifier_degraded" if result.degraded else "no_triage_answers")
    if worth < TRIAGE_WORTH_MIN or bucket == "discard":
        reason = "para_discard" if bucket == "discard" else "below_worth_threshold"
        return make("discard" if gate == "high" else "log", reason)
    if gate == "low":
        return make("log", "low_confidence")
    if gate == "high" and bucket in BUCKET_NOTE_CLASS and relation not in ("unclear", "duplicates"):
        return make("note", "high_confidence", "permanent" if ready else bucket)
    if relation == "duplicates" and gate == "high":
        return make("log", "duplicates_existing")
    reason = (
        "relation_unclear"
        if relation == "unclear"
        else ("bucket_unclear" if bucket == "inbox" else "medium_confidence")
    )
    return make("inbox", reason, bucket if bucket in BUCKET_NOTE_CLASS else None)


def gate_name(index: float) -> str:
    return _level_name(TRIAGE_CONFIDENCE_LEVELS, index)
