"""E2e entry point for the 40-case retrieval eval (same floors as integration)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from eval.harness import run

MIN_RECALL_AT_5 = 0.95
MIN_MRR = 0.85
MAX_STALE_DECISION_ERRORS = 0
MAX_DUPLICATES = 0
#: Mean statements per tool call. Each statement is a network round trip
#: against a remote database, so these ceilings are the remote-latency
#: budget: raise them only when a feature needs the statements, never to
#: make a regression pass. Baselines: search 1.7, bootstrap 3.7.
MAX_MEAN_ROUND_TRIPS_SEARCH = 3
MAX_MEAN_ROUND_TRIPS_BOOTSTRAP = 6


async def test_retrieval_quality_on_eval_fixture(unthrottled, seeded_mcp_world) -> None:
    client, seeded = seeded_mcp_world
    samples = int(os.environ.get("SUBZERO_EVAL_LATENCY_SAMPLES", "1"))
    report = await run(client, seeded, latency_samples=samples)

    if target := os.environ.get("SUBZERO_EVAL_REPORT"):
        Path(target).write_text(json.dumps(report, indent=2))

    assert not report["failures"], report["failures"]
    assert report["recall_at_5"] >= MIN_RECALL_AT_5
    assert report["mrr"] >= MIN_MRR
    assert report["stale_decision_errors"] <= MAX_STALE_DECISION_ERRORS
    assert report["duplicates"] <= MAX_DUPLICATES
    by_tool = report["by_tool"]
    assert by_tool["search"]["mean_round_trips"] <= MAX_MEAN_ROUND_TRIPS_SEARCH, by_tool["search"]
    assert by_tool["bootstrap"]["mean_round_trips"] <= MAX_MEAN_ROUND_TRIPS_BOOTSTRAP, by_tool[
        "bootstrap"
    ]
