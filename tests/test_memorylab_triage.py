"""The memory-lab routing policy, from Jev answers to an outcome and layers."""

from __future__ import annotations

from typing import Any

import pytest
from typesafe_sdk import Choice, Noul, Score

from subzero.classifier.base import Answer, ClassificationResult
from subzero.classifier.questions import (
    TRIAGE_PARA_CRITERIA,
    checkpoint_questions,
    triage_questions,
)
from subzero.memorylab.triage import triage


def result(
    *,
    worth: float = 0.9,
    gate: float = 2.0,
    bucket: str = "resource",
    bucket_confidence: float = 0.8,
    relation: str = "new",
    atomicity: float = 1.0,
    preference: float = 0.1,
    procedure: float = 0.1,
    decision: float = 0.1,
    degraded: bool = False,
) -> ClassificationResult:
    answers: dict[str, Answer] = {
        "worth_processing": Answer("noul", worth),
        "primary_intent": Answer("choice", "research", confidence=0.8),
        "has_decision": Answer("noul", decision),
        "has_preference": Answer("noul", preference),
        "has_procedure": Answer("noul", procedure),
        "atomicity": Answer(
            "score",
            atomicity,
            confidence=0.7,
            probabilities={"2": max(0.0, atomicity - 1.0), "1": 1.0 - max(0.0, atomicity - 1.0)},
        ),
        "para_bucket": Answer("choice", bucket, confidence=bucket_confidence),
        "relation_to_existing": Answer("choice", relation, confidence=0.7),
        "confidence_gate": Answer("score", gate, confidence=0.7),
    }
    return ClassificationResult(answers=answers, classifier="jev", degraded=degraded)


def test_questions_are_typed_and_in_the_checkpoint_request() -> None:
    questions = triage_questions()
    assert set(questions) == {
        "worth_processing",
        "primary_intent",
        "has_decision",
        "has_preference",
        "has_procedure",
        "atomicity",
        "para_bucket",
        "relation_to_existing",
        "confidence_gate",
    }
    kinds: dict[str, Any] = {k: type(v) for k, v in questions.items()}
    assert kinds["worth_processing"] is Noul
    assert kinds["para_bucket"] is Choice
    assert kinds["confidence_gate"] is Score
    # One request per session: the triage set rides along with the checkpoint set.
    assert set(questions) <= set(checkpoint_questions({}))
    # Every Choice offers a no-match option.
    assert "inbox" in TRIAGE_PARA_CRITERIA and "discard" in TRIAGE_PARA_CRITERIA


def test_high_confidence_clear_bucket_becomes_a_note() -> None:
    decision = triage(result(bucket="area"))
    assert (decision.outcome, decision.bucket, decision.note_class) == ("note", "area", "area")


def test_ready_permanent_goes_to_permanent() -> None:
    assert triage(result(atomicity=2.0)).note_class == "permanent"
    # An expected value of 1.76 is "mostly ready", not ready.
    assert triage(result(atomicity=1.76)).note_class == "resource"
    # Project material stays with the project however tidy it is.
    assert triage(result(atomicity=2.0, bucket="project")).note_class == "project"


def test_an_unsure_bucket_is_only_a_suggestion() -> None:
    decision = triage(result(bucket="area", bucket_confidence=0.4))
    assert (decision.outcome, decision.bucket) == ("inbox", "area")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"relation": "unclear"}, "relation_unclear"),
        ({"relation": "contradicts"}, "contradicts_existing"),
        ({"bucket": "inbox"}, "bucket_unclear"),
    ],
)
def test_only_valuable_ambiguous_sessions_reach_the_inbox(
    kwargs: dict[str, Any], reason: str
) -> None:
    decision = triage(result(**kwargs))
    assert (decision.outcome, decision.reason, decision.note_class) == ("inbox", reason, "inbox")
    assert "inbox" in decision.layers


def test_a_medium_gate_with_a_clear_home_is_still_a_note() -> None:
    # Claude reads the whole transcript before writing it, so Jev only has to
    # be sure where it goes, not of every detail.
    assert triage(result(gate=1.0)).outcome == "note"


def test_middling_sessions_stay_out_of_the_vault_and_the_inbox() -> None:
    decision = triage(result(worth=0.7, bucket="inbox", bucket_confidence=0.3))
    assert (decision.outcome, decision.reason) == ("log", "below_note_threshold")
    assert decision.layers == []
    # ...unless they carry a decision: then the structured layer extracts it.
    with_decision = triage(result(worth=0.7, decision=0.9))
    assert with_decision.outcome == "log"
    assert with_decision.layers == ["structured", "hot"]


def test_each_layer_gets_what_it_is_for() -> None:
    note = triage(result(preference=0.9))
    assert note.layers == ["vault", "structured", "honcho", "hot"]
    plain = triage(result())
    assert plain.layers == ["vault", "structured"]


def test_low_worth_is_logged_not_drafted() -> None:
    assert triage(result(worth=0.4, gate=1.0)).outcome == "log"
    # Jev sure it is worthless: discarded outright.
    assert triage(result(worth=0.4, gate=2.0)).outcome == "discard"


def test_low_gate_is_logged() -> None:
    decision = triage(result(gate=0.2))
    assert (decision.outcome, decision.reason) == ("log", "low_confidence")


def test_degraded_never_drafts() -> None:
    assert triage(result(degraded=True)).outcome == "log"
    empty = ClassificationResult(answers={}, classifier="rules")
    assert triage(empty).reason == "no_triage_answers"


def test_duplicate_is_not_written_twice() -> None:
    decision = triage(result(relation="duplicates", preference=0.9))
    assert (decision.outcome, decision.reason, decision.layers) == (
        "log",
        "duplicates_existing",
        [],
    )


def test_preferences_and_procedures_become_candidates() -> None:
    decision = triage(result(preference=0.9, procedure=0.85, gate=1.0))
    assert decision.candidates == ["preference", "procedure"]
    # Below the worth line nothing is a candidate.
    assert triage(result(worth=0.1, preference=0.95)).candidates == []


def test_checkpoint_result_carries_triage() -> None:
    from subzero.sessions.service import _triage_json

    payload = _triage_json(result(bucket="area"))
    assert payload["triage"]["outcome"] == "note"
    assert payload["triage"]["scores"]["para_bucket"] == "area"
    assert payload["triage"]["layers"] == ["vault", "structured"]
    assert _triage_json(ClassificationResult(answers={}, classifier="rules")) == {}
