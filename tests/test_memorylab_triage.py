"""The memory-lab decision policy, from Jev answers to one outcome."""

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
        ({"gate": 1.0}, "medium_confidence"),
        ({"relation": "unclear"}, "relation_unclear"),
        ({"bucket": "inbox"}, "bucket_unclear"),
    ],
)
def test_medium_or_unclear_is_parked_in_the_inbox(kwargs: dict[str, Any], reason: str) -> None:
    decision = triage(result(**kwargs))
    assert decision.outcome == "inbox"
    assert decision.reason == reason
    assert decision.note_class == "inbox"


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
    assert triage(result(relation="duplicates")).reason == "duplicates_existing"


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
    assert _triage_json(ClassificationResult(answers={}, classifier="rules")) == {}
