"""The on-machine runner, with a fake executor and a fake server.

Every test asserts the exact argv handed to tmux-axi or herdr-axi and the
exact finish payload posted for the job, not just the outcome: the runner is
the only code that touches a shell, so what it types is the contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from subzero.agents import runner
from subzero.agents.runner import Runner, RunnerSetupError, ToolRun

JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SESSION_NAME = "subzero-aaaaaaaa"
FM_REFUSAL = "firstmate crewmates get read-only herdr access (FM_TASK_ID is set)"


def tool_json(payload: dict[str, Any], returncode: int = 0) -> ToolRun:
    return ToolRun(returncode, json.dumps(payload), "")


class FakeExecutor:
    """Records every (argv, timeout) and replays canned ToolRuns in order."""

    def __init__(self, *responses: ToolRun) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self._responses = list(responses)

    def __call__(self, argv: list[str], timeout: float) -> ToolRun:
        self.calls.append((list(argv), timeout))
        if not self._responses:
            raise AssertionError(f"executor called more often than planned: {argv}")
        return self._responses.pop(0)

    @property
    def argv(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]


class FakeApi:
    """Stands in for client.api: one job to claim, finishes recorded."""

    def __init__(self, job: dict[str, Any] | None = None, board: dict[str, Any] | None = None):
        self.job = job
        self.board = board or {"jobs": []}
        self.claims: list[dict[str, Any]] = []
        self.finishes: list[tuple[str, dict[str, Any]]] = []
        self.paths: list[str] = []

    def __call__(self, method: str, path: str, **kwargs: Any) -> Any:
        self.paths.append(f"{method} {path}")
        if path == "/api/v1/dispatch/claim":
            body = kwargs.get("json_body") or {}
            self.claims.append(body)
            job, self.job = self.job, None
            return {"job": job}
        if path.startswith("/api/v1/dispatch/") and path.endswith("/finish"):
            self.finishes.append((path.split("/")[-2], kwargs.get("json_body") or {}))
            return {}
        if path == "/api/v1/agents/board":
            return self.board
        raise AssertionError(f"unexpected API call: {method} {path}")


def make_runner(
    tmp_path: Path,
    executor: FakeExecutor | None = None,
    api: FakeApi | None = None,
    *,
    tools: tuple[str, ...] = ("tmux-axi", "herdr-axi"),
    sleep: Any = None,
    **kwargs: Any,
) -> tuple[Runner, FakeExecutor, FakeApi, list[str]]:
    said: list[str] = []
    executor = executor or FakeExecutor()
    api = api or FakeApi()
    kwargs.setdefault("exec_roots", [str(tmp_path)])
    r = Runner(
        targets=["crew"],
        session_id="session-1",
        api=api,
        executor=executor,
        which=lambda name: f"/usr/local/bin/{name}" if name in tools else None,
        echo=said.append,
        sleep=sleep or (lambda seconds: None),
        **kwargs,
    )
    return r, executor, api, said


def command_job(cwd: str | None, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": JOB_ID,
        "kind": "command",
        "goal": "uv run pytest -q",
        "command": "uv run pytest -q",
        "first_command": None,
        "cwd": cwd,
        "worktree": None,
        "space": None,
        "timeout_s": 90,
        "keep_open": False,
        "target": "crew",
        "status": "claimed",
    }
    job.update(overrides)
    return job


# --------------------------------------------------------------- command


def test_command_success_argv_and_finish_payload(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        tool_json(
            {
                "session": "axi-1-1",
                "exit_code": 0,
                "duration_ms": 12,
                "timed_out": False,
                "stdout": "all passed\n",
                "stderr": "",
                "cwd": str(cwd.resolve()),
            }
        )
    )
    api = FakeApi(command_job(str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0

    assert executor.calls == [
        (
            [
                "tmux-axi",
                "run",
                "--json",
                "--full",
                "--command",
                "uv run pytest -q",
                "--cwd",
                str(cwd.resolve()),
                "--timeout",
                "90",
            ],
            90 + runner.GRACE_S,
        )
    ]
    assert api.claims == [{"session_id": "session-1", "targets": ["crew"]}]
    assert api.finishes == [
        (
            JOB_ID,
            {
                "status": "done",
                "result": "exit 0",
                "exit_code": 0,
                "output": "stdout:\nall passed\n",
            },
        )
    ]


def test_command_nonzero_exit_is_done_with_the_real_code(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        tool_json({"exit_code": 3, "timed_out": False, "stdout": "", "stderr": "boom\n"})
    )
    api = FakeApi(command_job(str(cwd), command="exit 3", goal="exit 3"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload == {
        "status": "done",
        "result": "exit 3",
        "exit_code": 3,
        "output": "stderr:\nboom\n",
    }


def test_command_timeout_is_failed_but_keeps_code_and_partial_output(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        tool_json(
            {"exit_code": 130, "timed_out": True, "stdout": "partial\n", "stderr": ""},
            returncode=1,
        )
    )
    api = FakeApi(command_job(str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 130
    assert payload["output"] == "stdout:\npartial\n"
    assert "killed by tmux-axi after 90s" in payload["result"]


def test_command_tool_error_is_failed_with_the_tools_words(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        tool_json(
            {"error": "tmux is not installed", "suggestion": "brew install tmux"}, returncode=1
        )
    )
    api = FakeApi(command_job(str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload == {
        "status": "failed",
        "result": (
            "tmux-axi could not run the command: tmux is not installed "
            "(suggestion: brew install tmux)"
        ),
    }


def test_command_unparseable_output_is_failed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(ToolRun(0, "TOON surprise\n", ""))
    api = FakeApi(command_job(str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "unparseable" in payload["result"]


def test_command_output_longer_than_the_stored_tail_says_the_true_total(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    big = "x" * (runner.OUTPUT_TAIL_CHARS + 5000)
    executor = FakeExecutor(tool_json({"exit_code": 0, "timed_out": False, "stdout": big}))
    api = FakeApi(command_job(str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert payload["output"] == f"stdout:\n{big}"
    total = len(big) + len("stdout:\n")
    assert f"the last {runner.OUTPUT_TAIL_CHARS} of {total} chars" in payload["result"]


# ------------------------------------------------------- local placement


def test_cwd_outside_the_local_roots_is_refused_without_running(tmp_path: Path) -> None:
    executor = FakeExecutor()
    api = FakeApi(command_job("/etc"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.calls == []
    ((_, payload),) = api.finishes
    assert payload == {
        "status": "failed",
        "result": "refused: worktree '/etc' resolves outside every configured exec root.",
    }


def test_cwd_escaping_through_a_symlink_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "escape").symlink_to(outside)
    executor = FakeExecutor()
    api = FakeApi(command_job(str(roots / "escape")))
    r, _, _, _ = make_runner(roots, executor, api)
    assert r.run() == 0
    assert executor.calls == []
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "outside every configured exec root" in payload["result"]


def test_command_without_cwd_is_refused(tmp_path: Path) -> None:
    executor = FakeExecutor()
    api = FakeApi(command_job(None))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.calls == []
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "no cwd" in payload["result"]


def test_local_exec_roots_parses_env_and_defaults() -> None:
    assert runner.local_exec_roots({}) == ["~/Code", "~/Code/worktrees"]
    assert runner.local_exec_roots({"SUBZERO_EXEC_ROOTS": "~/a, ~/b"}) == ["~/a", "~/b"]
    assert runner.local_exec_roots({"SUBZERO_EXEC_ROOTS": '["/x", "/y"]'}) == ["/x", "/y"]
    with pytest.raises(RunnerSetupError):
        runner.local_exec_roots({"SUBZERO_EXEC_ROOTS": "[oops"})


# ---------------------------------------------------------- environment


def test_missing_tmux_axi_fails_fast_before_any_claim(tmp_path: Path) -> None:
    api = FakeApi(command_job(str(tmp_path)))
    r, executor, _, _ = make_runner(tmp_path, None, api, tools=("herdr-axi",))
    with pytest.raises(RunnerSetupError, match="tmux-axi is not on PATH"):
        r.run()
    assert api.paths == []
    assert executor.calls == []


def test_missing_herdr_axi_with_open_fails_fast(tmp_path: Path) -> None:
    r, _, _, _ = make_runner(tmp_path, tools=("tmux-axi",), open_repo=True)
    with pytest.raises(RunnerSetupError, match="herdr-axi is not on PATH"):
        r.run()


def test_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.os, "geteuid", lambda: 0, raising=False)
    r, _, _, _ = make_runner(tmp_path)
    with pytest.raises(RunnerSetupError, match="root"):
        r.run()


def test_relative_exec_root_is_refused(tmp_path: Path) -> None:
    r, _, _, _ = make_runner(tmp_path, exec_roots=["relative/roots"])
    with pytest.raises(RunnerSetupError, match="absolute directories"):
        r.run()


# ------------------------------------------------------------------ loop


def test_once_with_empty_queue_says_so_and_exits_zero(tmp_path: Path) -> None:
    api = FakeApi(None)
    r, _, _, said = make_runner(tmp_path, None, api)
    assert r.run() == 0
    assert any("Nothing queued for >crew" in line for line in said)
    assert api.finishes == []


def test_loop_sleeps_between_empty_polls_and_stops_on_interrupt(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def sleep_then_stop(seconds: float) -> None:
        sleeps.append(seconds)
        raise KeyboardInterrupt

    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(tool_json({"exit_code": 0, "timed_out": False, "stdout": ""}))
    api = FakeApi(command_job(str(cwd)))
    r, _, _, said = make_runner(tmp_path, executor, api, sleep=sleep_then_stop)
    assert r.run(loop=True, interval=2.0) == 0
    assert sleeps == [2.0]
    assert len(api.finishes) == 1
    assert "Stopped." in said


# ---------------------------------------------------------------- session


def session_job(**overrides: Any) -> dict[str, Any]:
    job = command_job(None, kind="session", command=None, timeout_s=None, keep_open=True)
    job.update({"goal": "Open an interactive session in repo."})
    job.update(overrides)
    return job


def open_ok() -> ToolRun:
    return tool_json(
        {
            "session": SESSION_NAME,
            "app": "ghostty",
            "launched": "ghostty on PATH",
            "attached_window": "0:zsh",
            "attach": f"tmux attach -t {SESSION_NAME}",
        }
    )


def test_session_without_first_command_opens_and_finishes(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(open_ok())
    api = FakeApi(session_job(cwd=str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.argv == [
        [
            "tmux-axi",
            "open",
            "--session",
            SESSION_NAME,
            "--cwd",
            str(cwd.resolve()),
            "--app",
            "ghostty",
            "--json",
        ]
    ]
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert f"tmux attach -t {SESSION_NAME}" in payload["result"]
    assert "exit_code" not in payload


def test_session_types_first_command_without_enter(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        open_ok(), tool_json({"session": SESSION_NAME, "sent": "git status", "enter": False})
    )
    api = FakeApi(session_job(cwd=str(cwd), first_command="git status"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.argv[1] == [
        "tmux-axi",
        "send",
        "--session",
        SESSION_NAME,
        "--keys",
        "git status",
    ]
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert "Enter was NOT sent" in payload["result"]
    assert "typed for the human to review" in payload["result"]


def test_session_sends_enter_only_for_a_trailing_newline(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        open_ok(), tool_json({"session": SESSION_NAME, "sent": "npm test", "enter": True})
    )
    api = FakeApi(session_job(cwd=str(cwd), first_command="npm test\n"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.argv[1] == [
        "tmux-axi",
        "send",
        "--session",
        SESSION_NAME,
        "--keys",
        "npm test",
        "--enter",
    ]
    ((_, payload),) = api.finishes
    assert "Enter was sent" in payload["result"]


def test_session_enter_rule_is_a_pure_function() -> None:
    assert "--enter" not in runner.send_argv("s", "ls -la")
    assert runner.send_argv("s", "ls -la\n") == [
        "tmux-axi",
        "send",
        "--session",
        "s",
        "--keys",
        "ls -la",
        "--enter",
    ]


def test_session_without_gui_keeps_the_detached_session_and_says_so(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(
        tool_json(
            {
                "error": "cannot launch 'ghostty': no 'ghostty' executable on PATH",
                "suggestion": f"attach with: tmux attach -t {SESSION_NAME}",
            },
            returncode=1,
        )
    )
    api = FakeApi(session_job(cwd=str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert "could not launch a terminal window" in payload["result"]
    assert f"detached session {SESSION_NAME}" in payload["result"]


def test_session_open_failure_other_than_gui_is_failed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor(tool_json({"error": "tmux server refused the session"}, returncode=1))
    api = FakeApi(session_job(cwd=str(cwd)))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "tmux server refused the session" in payload["result"]


# ------------------------------------------------------------------- goal


def goal_job(**overrides: Any) -> dict[str, Any]:
    job = command_job(None, kind="goal", command=None, timeout_s=None)
    job.update({"goal": "add a dark mode toggle"})
    job.update(overrides)
    return job


def panes_json(panes: list[dict[str, Any]]) -> ToolRun:
    return tool_json({"count": len(panes), "panes": panes})


def pane(pane_id: str, agent_state: str, foreground: str = "zsh") -> dict[str, Any]:
    return {"id": pane_id, "label": pane_id, "foreground": foreground, "agent_state": agent_state}


def test_goal_without_space_is_printed_and_never_executed(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    executor = FakeExecutor()
    job = goal_job(cwd=str(cwd.resolve()), worktree="repo")
    api = FakeApi(job)
    r, _, _, said = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.calls == []
    assert json.dumps(job) in said
    assert f"cwd: {cwd.resolve()}" in said
    ((_, payload),) = api.finishes
    assert payload == {
        "status": "done",
        "result": "printed the job for a human; the runner never executes a goal.",
    }


def test_goal_open_uses_the_worktree_repo_and_slug(tmp_path: Path) -> None:
    executor = FakeExecutor(
        tool_json(
            {
                "opener": "dev",
                "workspace": "w77",
                "label": "subzero/cli-exec",
                "cwd": "/x",
                "tabs": ["map", "git", "code"],
            }
        )
    )
    api = FakeApi(goal_job(worktree="subzero/cli-exec", cwd=str(tmp_path)))
    r, _, _, _ = make_runner(tmp_path, executor, api, open_repo=True)
    assert r.run() == 0
    assert executor.argv == [
        ["herdr-axi", "open", "--repo", "subzero", "--slug", "cli-exec", "--json"]
    ]
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert "workspace w77" in payload["result"]


def test_goal_with_space_and_explicit_pane_submits_verbatim(tmp_path: Path) -> None:
    goal = "add a dark mode toggle"
    executor = FakeExecutor(
        panes_json([pane("w5W:p1", "none"), pane("w5W:p2", "omp:idle", "omp")]),
        tool_json({"pane": "w5W:p1", "submitted": goal, "command": goal}),
    )
    api = FakeApi(goal_job(space="w5W", goal=goal))
    r, _, _, _ = make_runner(tmp_path, executor, api, pane="w5W:p1")
    assert r.run() == 0
    assert executor.argv == [
        ["herdr-axi", "panes", "--space", "w5W", "--json"],
        ["herdr-axi", "run", "--pane", "w5W:p1", "--command", goal, "--json"],
    ]
    ((_, payload),) = api.finishes
    assert payload["status"] == "done"
    assert "pane w5W:p1" in payload["result"]
    assert "did not observe a result" in payload["result"]


def test_goal_with_space_picks_the_only_agent_pane(tmp_path: Path) -> None:
    executor = FakeExecutor(
        panes_json(
            [pane("w5W:p1", "none"), pane("w5W:p2", "unknown"), pane("w5W:p3", "omp:idle", "omp")]
        ),
        tool_json({"pane": "w5W:p3", "submitted": "x", "command": "x"}),
    )
    api = FakeApi(goal_job(space="w5W"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert executor.argv[1][:4] == ["herdr-axi", "run", "--pane", "w5W:p3"]


def test_goal_with_space_refuses_to_guess_between_agent_panes(tmp_path: Path) -> None:
    executor = FakeExecutor(
        panes_json([pane("w5W:p1", "omp:idle"), pane("w5W:p2", "claude:working")])
    )
    api = FakeApi(goal_job(space="w5W"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert len(executor.argv) == 1  # never submitted anywhere
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "w5W:p1" in payload["result"] and "w5W:p2" in payload["result"]
    assert "--pane" in payload["result"]


def test_goal_with_space_and_no_agent_pane_fails_with_the_list(tmp_path: Path) -> None:
    executor = FakeExecutor(panes_json([pane("w5W:p1", "none"), pane("w5W:p2", "unknown")]))
    api = FakeApi(goal_job(space="w5W"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert len(executor.argv) == 1
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "cannot pick a pane" in payload["result"]


def test_fm_task_id_refusal_is_surfaced_verbatim_without_retry(tmp_path: Path) -> None:
    executor = FakeExecutor(
        panes_json([pane("w5W:p2", "omp:idle")]),
        tool_json(
            {"error": FM_REFUSAL, "suggestion": "allowed read-only commands: ..."},
            returncode=2,
        ),
    )
    api = FakeApi(goal_job(space="w5W"))
    r, _, _, _ = make_runner(tmp_path, executor, api)
    assert r.run() == 0
    assert len(executor.argv) == 2  # panes, one refused run, no retry
    ((_, payload),) = api.finishes
    assert payload == {"status": "failed", "result": FM_REFUSAL}


def test_goal_with_space_and_missing_herdr_is_failed(tmp_path: Path) -> None:
    executor = FakeExecutor()
    api = FakeApi(goal_job(space="w5W"))
    r, _, _, _ = make_runner(tmp_path, executor, api, tools=("tmux-axi",))
    assert r.run() == 0
    assert executor.calls == []
    ((_, payload),) = api.finishes
    assert payload["status"] == "failed"
    assert "herdr-axi is not on PATH" in payload["result"]


# --------------------------------------------------------------- dry run


def test_dry_run_claims_nothing_and_prints_the_plan(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    cwd.mkdir()
    queued = {**command_job(str(cwd.resolve())), "status": "queued"}
    board = {"jobs": [queued, {**queued, "id": "other-job", "status": "claimed"}]}
    executor = FakeExecutor()
    api = FakeApi(None, board=board)
    r, _, _, said = make_runner(tmp_path, executor, api)
    assert r.run(dry_run=True) == 0
    assert executor.calls == []
    assert api.finishes == []
    assert api.paths == ["GET /api/v1/agents/board"]
    assert any("tmux-axi run --json --full --command" in line for line in said)
    assert any("nothing was claimed" in line.lower() for line in said)


def test_tail_for_finish_only_notes_a_real_cut() -> None:
    assert runner.tail_for_finish(None) == (None, None)
    short = "x" * 100
    assert runner.tail_for_finish(short) == (short, None)
    long = "y" * (runner.SEND_OUTPUT_CAP + 10)
    send, note = runner.tail_for_finish(long)
    assert send is not None and len(send) == runner.SEND_OUTPUT_CAP
    assert note is not None and str(len(long)) in note
