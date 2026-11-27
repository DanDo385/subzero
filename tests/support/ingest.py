"""Extension fixture payloads and paths for ingest tests."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "extension" / "tests" / "fixtures"


def load_fixture(provider: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{provider}-conversation.json").read_text())
