"""E2e entry point for the 40-case retrieval eval (same floors as integration)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eval.harness import QueryCounter, run

MIN_RECALL_AT_5 = 0.95
MIN_MRR = 0.85
MAX_STALE_DECISION_ERRORS = 0
MAX_DUPLICATES = 0


async def test_retrieval_quality_on_eval_fixture(
    unthrottled, seeded_mcp_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, seeded = seeded_mcp_world
    counter = QueryCounter(monkeypatch)
    samples = int(os.environ.get("SUBZERO_EVAL_LATENCY_SAMPLES", "1"))
    report = await run(client, seeded, counter, latency_samples=samples)

    if target := os.environ.get("SUBZERO_EVAL_REPORT"):
        Path(target).write_text(json.dumps(report, indent=2))

    assert not report["failures"], report["failures"]
    assert report["recall_at_5"] >= MIN_RECALL_AT_5
    assert report["mrr"] >= MIN_MRR
    assert report["stale_decision_errors"] <= MAX_STALE_DECISION_ERRORS
    assert report["duplicates"] <= MAX_DUPLICATES
