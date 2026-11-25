"""Body of one CLI job. Runs inside the job's tmux session.

Stdlib only. The parent writes ``meta.json``, ``env.json`` and ``command``
into the job directory and passes that directory as argv. The user command
is started with the env file, never with this process's environment, so a
secret in the server process does not become a shell variable.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def _publish(job: Path, name: str, text: str) -> None:
    path = job / name
    tmp = job / f".{name}.tmp"
    tmp.write_text(text)
    tmp.replace(path)


def _pump(src: object, dest: Path, mirror: object) -> None:
    reader = getattr(src, "read", None)
    if reader is None:
        return
    with dest.open("wb") as handle:
        while True:
            chunk = reader(4096)
            if not chunk:
                break
            handle.write(chunk)
            handle.flush()
            try:
                mirror.write(chunk)  # type: ignore[attr-defined]
                mirror.flush()  # type: ignore[attr-defined]
            except (OSError, ValueError):
                mirror = _Sink()


class _Sink:
    def write(self, _chunk: bytes) -> None:
        return None

    def flush(self) -> None:
        return None


def _run(job: Path) -> int:
    meta = json.loads((job / "meta.json").read_text())
    env = {
        str(key): str(value) for key, value in json.loads((job / "env.json").read_text()).items()
    }
    command = (job / "command").read_text()
    timeout_s = float(meta["timeout_s"])
    proc = subprocess.Popen(  # noqa: S603
        ["/bin/bash", "--noprofile", "--norc", "-c", command],
        cwd=meta["cwd"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _publish(job, "command_pid", str(proc.pid))
    timed_out = threading.Event()

    def _kill(sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return

    def _fire() -> None:
        timed_out.set()
        _kill(signal.SIGTERM)
        threading.Timer(1.0, lambda: _kill(signal.SIGKILL)).start()

    timer = threading.Timer(timeout_s, _fire)
    timer.daemon = True
    stdout_thread = threading.Thread(
        target=_pump,
        args=(proc.stdout, job / "stdout", getattr(sys.stdout, "buffer", _Sink())),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_pump,
        args=(proc.stderr, job / "stderr", getattr(sys.stderr, "buffer", _Sink())),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timer.start()
    try:
        try:
            proc.wait(timeout=timeout_s + 3)
        except subprocess.TimeoutExpired:
            timed_out.set()
            _kill(signal.SIGKILL)
            proc.wait(timeout=2)
    finally:
        timer.cancel()
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    code = proc.returncode if proc.returncode is not None else 124
    _publish(job, "exit", str(code))
    _publish(job, "status", "timed_out" if timed_out.is_set() else "done")
    if meta.get("retain"):
        os.chdir(meta["cwd"])
        os.execv("/bin/bash", ["/bin/bash", "--noprofile", "--norc"])  # noqa: S606
    return code


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: session.py JOB_DIR", file=sys.stderr)
        return 2
    job = Path(sys.argv[1])
    try:
        return _run(job)
    except Exception as exc:
        try:
            _publish(job, "stderr", f"{exc}\n")
            _publish(job, "exit", "1")
            _publish(job, "status", "failed")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
