"""Agent sessions, messages and dispatch, through the service and over HTTP."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from conftest import requires_db
from subzero.classifier.base import Answer, ClassificationResult
from subzero.classifier.questions import QUESTIONS_VERSION
from subzero.errors import ValidationError

pytestmark = requires_db


class _Jev:
    """Answers the dispatch question with a fixed pick and confidence."""

    def __init__(self, pick: str, confidence: float, *, degraded: bool = False) -> None:
        self.pick, self.confidence, self.degraded = pick, confidence, degraded

    async def classify_dispatch(self, state: dict[str, Any], candidates: dict[str, str]):
        return ClassificationResult(
            answers={
                "target": Answer(
                    "choice",
                    self.pick,
                    confidence=self.confidence,
                    probabilities={self.pick: self.confidence},
                )
            },
            classifier="jev",
            model="test",
            questions_version=QUESTIONS_VERSION,
            input_hash="x",
            degraded=self.degraded,
        )


async def _session(services, harness="claude-code", home=None, ext=None) -> dict:
    return await services.agents.start(
        harness=harness, external_id=ext, machine="mbp", cwd="/Users/me/Code/subzero", home=home
    )


# ------------------------------------------------------------------ sessions


async def test_same_external_id_is_one_session(services) -> None:
    a = await _session(services, ext="claude:abc")
    b = await _session(services, ext="claude:abc")
    assert a["id"] == b["id"]
    assert a["name"] == "claude-code@mbp:subzero"
    kinds = [r["kind"] for r in await services.db.fetch("SELECT kind FROM agent_events")]
    assert kinds == ["spawn"]


async def test_beat_moves_and_reports_status(services) -> None:
    s = await _session(services)
    sid = UUID(s["id"])
    out = await services.agents.beat(
        sid, status="waiting", task="Type the comptime fn in src/list.zig:30", home="lesson/zig"
    )
    assert out["session"]["status"] == "waiting"
    assert out["session"]["home"] == "lesson/zig"
    kinds = [
        r["kind"] for r in await services.db.fetch("SELECT kind FROM agent_events ORDER BY id")
    ]
    assert kinds == ["spawn", "move", "status"]
    with pytest.raises(ValidationError):
        await services.agents.beat(sid, status="sleeping")


async def test_end_finishes_a_claimed_job(services) -> None:
    s = await _session(services)
    job = await services.agents.dispatch("tidy the README >claude")
    await services.agents.claim(UUID(s["id"]), targets=["claude"])
    await services.agents.end(UUID(s["id"]), summary="README tidied")
    row = await services.db.fetchrow(
        "SELECT status, result FROM dispatch_jobs WHERE id = $1", UUID(job["id"])
    )
    assert (row["status"], row["result"]) == ("done", "README tidied")


# ------------------------------------------------------------------ messages


async def test_direct_message_is_delivered_once(services) -> None:
    a, b = await _session(services, ext="a"), await _session(services, ext="b")
    await services.agents.send(
        to=b["id"][:8],
        body="rebase on main first",
        from_session=UUID(a["id"]),
        from_label=a["name"],
    )
    first = await services.agents.beat(UUID(b["id"]))
    second = await services.agents.beat(UUID(b["id"]))
    assert [m["body"] for m in first["messages"]] == ["rebase on main first"]
    assert second["messages"] == []


async def test_home_message_reaches_each_session_there_but_not_the_sender(services) -> None:
    a = await _session(services, ext="a", home="lesson/zig")
    b = await _session(services, ext="b", home="lesson/zig")
    c = await _session(services, ext="c", home="projects/other")
    await services.agents.send(to="^zig", body="use 0.15 syntax", from_session=UUID(a["id"]))
    assert (await services.agents.beat(UUID(a["id"])))["messages"] == []
    assert len((await services.agents.beat(UUID(b["id"])))["messages"]) == 1
    assert (await services.agents.beat(UUID(b["id"])))["messages"] == []
    assert (await services.agents.beat(UUID(c["id"])))["messages"] == []


async def test_messages_for_me(services) -> None:
    s = await _session(services)
    await services.agents.send(to="me", body="your turn in list.zig", from_session=UUID(s["id"]))
    assert [m["body"] for m in await services.agents.messages_for_me()] == ["your turn in list.zig"]
    assert await services.agents.messages_for_me() == []


async def test_bad_addressees_are_refused(services) -> None:
    with pytest.raises(ValidationError):
        await services.agents.send(to="^", body="x")
    with pytest.raises(ValidationError):
        await services.agents.send(to="me", body="   ")


# ------------------------------------------------------------------ dispatch


async def test_target_sigil_routes_without_jev(services) -> None:
    job = await services.agents.dispatch("^zig scaffold the allocator lesson >claude", mode="ship")
    assert (job["target"], job["routed_by"], job["home"], job["mode"]) == (
        "claude",
        "sigil",
        "lesson/zig",
        "ship",
    )


async def test_risky_ship_starts_as_scout(services) -> None:
    job = await services.agents.dispatch(
        "lock down the tailscale ACL on hetzner >crew", mode="ship"
    )
    assert job["mode"] == "scout"
    assert job["notes"]


async def test_unknown_target_is_refused(services) -> None:
    with pytest.raises(ValidationError):
        await services.agents.dispatch("do it >nobody")


async def test_confident_jev_picks_the_agent(services) -> None:
    services.agents._classifier = _Jev("grok", 0.82)
    job = await services.agents.dispatch("who on X is talking about zig 0.16")
    assert (job["target"], job["routed_by"]) == ("grok", "jev")


async def test_unsure_or_unavailable_jev_goes_to_crew(services) -> None:
    services.agents._classifier = _Jev("grok", 0.41)
    assert (await services.agents.dispatch("something vague"))["target"] == "crew"
    services.agents._classifier = _Jev("grok", 0.99, degraded=True)
    job = await services.agents.dispatch("something else")
    assert (job["target"], job["routed_by"]) == ("crew", "default")
    assert any("unavailable" in n for n in job["notes"])


async def test_job_for_me_also_messages_me(services) -> None:
    await services.agents.dispatch("decide the CFA L3 pathway >me")
    assert "decide the CFA L3 pathway" in (await services.agents.messages_for_me())[0]["body"]


async def test_claim_prefers_named_jobs_then_crew(services) -> None:
    await services.agents.dispatch("anyone can do this >crew")
    named = await services.agents.dispatch("only for claude >claude")
    await services.agents.dispatch("only for grok >grok")
    s = await _session(services)
    first = await services.agents.claim(UUID(s["id"]), targets=["claude"])
    second = await services.agents.claim(UUID(s["id"]), targets=["claude"])
    third = await services.agents.claim(UUID(s["id"]), targets=["claude"])
    assert first["id"] == named["id"]
    assert second["target"] == "crew"
    assert third is None
    session = await services.db.fetchrow(
        "SELECT job_id, task FROM agent_sessions WHERE id = $1", UUID(s["id"])
    )
    assert str(session["job_id"]) == second["id"]


async def test_finish_records_the_result(services) -> None:
    job = await services.agents.dispatch("plan it >codex")
    done = await services.agents.finish(
        UUID(job["id"]), status="failed", result="tests would not run"
    )
    assert (done["status"], done["result"]) == ("failed", "tests would not run")
    with pytest.raises(ValidationError):
        await services.agents.finish(UUID(job["id"]), status="maybe")


# ------------------------------------------------------------------ board


async def test_board_marks_quiet_sessions_idle_and_streams_events(services) -> None:
    s = await _session(services)
    await services.db.execute(
        "UPDATE agent_sessions SET last_seen_at = now() - $1::interval WHERE id = $2",
        timedelta(hours=2),
        UUID(s["id"]),
    )
    board = await services.agents.board()
    assert board["sessions"][0]["status"] == "idle"
    last = board["events"][-1]["id"]
    await services.agents.dispatch("next thing >claude")
    newer = await services.agents.board(after_event=last)
    assert [e["kind"] for e in newer["events"]] == ["dispatch"]


# ------------------------------------------------------------------ HTTP


@pytest.fixture
async def http(services, live_server):
    async with httpx.AsyncClient(base_url=str(live_server.base_url)) as client:
        await services.db.execute(
            "INSERT INTO pairing_codes (code, device_name) VALUES ('AGENTS42', 'mbp')"
        )
        paired = await client.post(
            "/api/v1/auth/pair-device", json={"pairing_code": "AGENTS42", "device_name": "mbp"}
        )
        client.headers["authorization"] = f"Bearer {paired.json()['device_token']}"
        yield client


async def test_a_hook_session_round_trip_over_http(http) -> None:
    started = await http.post(
        "/api/v1/agents/start",
        json={
            "harness": "claude-code",
            "external_id": "cc-1",
            "cwd": "/w/subzero",
            "home": "lesson/zig",
        },
    )
    assert started.status_code == 200, started.text
    sid = started.json()["id"]
    assert started.json()["machine"] == "mbp"  # the paired device's name

    sent = await http.post("/api/v1/messages", json={"to": "^zig", "body": "hello from the board"})
    assert sent.status_code == 200, sent.text
    beat = await http.post(f"/api/v1/agents/{sid}/beat", json={"status": "working"})
    assert [m["body"] for m in beat.json()["messages"]] == ["hello from the board"]

    queued = await http.post("/api/v1/dispatch", json={"goal": "^zig write the test >claude"})
    claimed = await http.post(
        "/api/v1/dispatch/claim", json={"session_id": sid, "targets": ["claude"]}
    )
    assert claimed.json()["job"]["id"] == queued.json()["id"]
    finished = await http.post(
        f"/api/v1/dispatch/{queued.json()['id']}/finish", json={"result": "ok"}
    )
    assert finished.json()["status"] == "done"

    board = await http.get("/api/v1/agents/board")
    assert {e["kind"] for e in board.json()["events"]} >= {
        "spawn",
        "message",
        "dispatch",
        "claim",
        "job_done",
    }
    assert (await http.post(f"/api/v1/agents/{sid}/end", json={})).json()["status"] == "done"


async def test_http_errors_are_plain(http) -> None:
    assert (await http.post("/api/v1/agents/not-an-id/beat", json={})).status_code == 400
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await http.post(f"/api/v1/agents/{missing}/beat", json={})).status_code == 404
    assert (await http.post("/api/v1/dispatch", content=b"[]")).status_code == 400


async def test_bootstrap_over_http_reads_sigils(http) -> None:
    response = await http.get("/api/v1/bootstrap", params={"q": "^cfa2 duration"})
    assert response.status_code == 200
    assert any("Sigils read" in n for n in response.json().get("notes", []))
