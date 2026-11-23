"""Route one session to the memory layers that fit it, from Jev's answers.

Four layers, each fed what it is good at (docs/guides/16-memory-lab.md):

* ``vault``: a PARA note Claude writes from the full transcript (extraction),
  in the bucket Jev chose. For valuable sessions with a clear home.
* ``structured``: facts, events (decisions), instructions (preferences) and
  tasks, extracted by Claude into Subzero's memories, decisions and lists.
  For any worthwhile session that carries a decision, procedure or preference.
* ``honcho``: the person's own messages from sessions that lean towards a
  preference or working pattern (TRIAGE_HONCHO_MIN), with their original dates, so Honcho can
  model how preferences drift.
* ``hot``: candidates for MEMORY.md, USER.md and skills. Never written
  automatically; the weekly analysis promotes.

Exactly one vault outcome per session, first match wins:

1. Jev not reached: ``log``.
2. ``worth_processing`` under TRIAGE_WORTH_MIN, or bucket ``discard``:
   ``discard`` when the gate is high, else ``log``.
3. Low ``confidence_gate``, or a duplicate of something known: ``log``.
4. Worth at least TRIAGE_NOTE_WORTH, relation ``new`` or ``extends``, and a
   clear bucket (Choice confidence at least TRIAGE_BUCKET_MIN) or one torn
   between two TRIAGE_INTERCHANGEABLE homes: ``note``. A torn choice is
   ``tentative``; recover.py settles it in code (project link or not).
   ``permanent`` only for a clear resource of an insight-type intent, no
   procedure, that Jev is TRIAGE_PERMANENT_MIN sure stands alone.
5. Valuable but torn towards ``discard``: ``log`` (maybe disposable).
6. Valuable but Jev chose ``inbox``, or it contradicts or relates unclearly
   to what is known: ``inbox``. Only these need a person.
7. Anything else worth processing: ``log`` (it may still feed other layers).

Every threshold lives in classifier/questions.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from subzero.classifier.base import ClassificationResult
from subzero.classifier.questions import (
    TRIAGE_ATOMICITY_LEVELS,
    TRIAGE_BUCKET_MIN,
    TRIAGE_CANDIDATE_MIN,
    TRIAGE_GATE_HIGH,
    TRIAGE_GATE_MEDIUM,
    TRIAGE_HONCHO_MIN,
    TRIAGE_INTERCHANGEABLE,
    TRIAGE_NOTE_WORTH,
    TRIAGE_PERMANENT_INTENTS,
    TRIAGE_PERMANENT_MIN,
    TRIAGE_WORTH_MIN,
)

OUTCOMES = ("note", "inbox", "log", "discard")
LAYERS = ("vault", "inbox", "structured", "honcho", "hot")

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
    layers: list[str] = field(default_factory=list)
    #: Jev was torn between two interchangeable PARA homes; recover.py settles
    #: it from whether the session belongs to a known project.
    tentative: bool = False

    @property
    def note_class(self) -> str:
        if self.outcome == "note" and self.bucket:
            return BUCKET_NOTE_CLASS[self.bucket]
        return "inbox"


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
    bucket, bucket_confidence = result.choice("para_bucket")
    relation, _ = result.choice("relation_to_existing")
    # Score answers are expected values (1.76 is "mostly ready"), so read the
    # probability of the top level instead of rounding the value.
    atomicity = result.answers.get("atomicity")
    top_level = str(len(TRIAGE_ATOMICITY_LEVELS) - 1)
    ready_p = float(atomicity.probabilities.get(top_level, 0.0)) if atomicity else 0.0
    clear_bucket = bucket in BUCKET_NOTE_CLASS and bucket_confidence >= TRIAGE_BUCKET_MIN
    para = result.answers.get("para_bucket")
    ranked = sorted((para.probabilities if para else {}).items(), key=lambda kv: -kv[1])
    runner_up = ranked[1][0] if len(ranked) > 1 else None
    torn = (
        not clear_bucket
        and bucket in TRIAGE_INTERCHANGEABLE
        and runner_up in TRIAGE_INTERCHANGEABLE
    )
    intent, _ = result.choice("primary_intent")

    signals = {
        kind: result.noul(key, 0.0) >= TRIAGE_CANDIDATE_MIN
        for kind, key in (
            ("preference", "has_preference"),
            ("procedure", "has_procedure"),
            ("decision", "has_decision"),
        )
    }

    honcho_p = result.noul("has_preference", 0.0)

    def make(
        outcome: str, reason: str, chosen: str | None = None, tentative: bool = False
    ) -> Triage:
        usable = outcome != "discard" and worth >= TRIAGE_WORTH_MIN and gate != "low"
        usable = usable and reason not in ("duplicates_existing", "classifier_degraded")
        candidates = [k for k, yes in signals.items() if yes] if usable else []
        layers: list[str] = []
        if outcome == "note":
            layers.append("vault")
        if outcome == "inbox":
            layers.append("inbox")
        if usable and (outcome == "note" or candidates):
            layers.append("structured")
        if usable and honcho_p >= TRIAGE_HONCHO_MIN:
            layers.append("honcho")
        if candidates:
            layers.append("hot")
        return Triage(outcome, chosen, reason, gate, candidates, scores, layers, tentative)

    if result.degraded or not has_triage(result):
        return make("log", "classifier_degraded" if result.degraded else "no_triage_answers")
    if worth < TRIAGE_WORTH_MIN or bucket == "discard":
        reason = "para_discard" if bucket == "discard" else "below_worth_threshold"
        return make("discard" if gate == "high" else "log", reason)
    if gate == "low":
        return make("log", "low_confidence")
    if relation == "duplicates":
        return make("log", "duplicates_existing")
    if worth >= TRIAGE_NOTE_WORTH and (clear_bucket or torn) and relation in ("new", "extends"):
        # Permanent is for a standalone idea; project and area material and
        # procedures stay where they are used, however tidy they are.
        permanent = (
            clear_bucket
            and bucket == "resource"
            and ready_p >= TRIAGE_PERMANENT_MIN
            and intent in TRIAGE_PERMANENT_INTENTS
            and not signals["procedure"]
        )
        if permanent:
            return make("note", "clear_home", "permanent")
        return make("note", "clear_home" if clear_bucket else "tentative_home", bucket, torn)
    if worth >= TRIAGE_NOTE_WORTH and not clear_bucket and "discard" in (bucket, runner_up):
        return make("log", "maybe_disposable")
    if worth >= TRIAGE_NOTE_WORTH:
        reason = (
            "contradicts_existing"
            if relation == "contradicts"
            else "relation_unclear"
            if relation == "unclear"
            else "bucket_unclear"
        )
        return make("inbox", reason, bucket if bucket in BUCKET_NOTE_CLASS else None)
    return make("log", "below_note_threshold")
