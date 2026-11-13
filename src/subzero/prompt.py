"""The master prompt: the standing instruction every session starts with.

``subzero prompt`` prints it; the SessionStart hook injects it into Claude
Code (and any harness that runs the same hook). It is assembled from:

1. CORE: what subzero is, the sigils, when to save, messages and lessons.
2. AGENTS.md from the repo the session runs in (skipped for harnesses that
   already load it themselves, like Claude Code with its CLAUDE.md symlink).
3. MEMORY.md and MACHINES.md from ~/.config/subzero (your own; templates are
   copied there the first time).
4. Live context: the bootstrap package for this folder's project or lesson,
   and any messages waiting for this session.

The whole thing stays under MAX_CHARS so a hook never floods the context.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from subzero.client import CONFIG_DIR

MAX_CHARS = 9000
PERSONAL_FILES = ("MEMORY.md", "MACHINES.md")

CORE = """\
# subzero

You are one of several AI sessions (chat apps, Claude Code, Codex, Grok, firstmate crews)
that share one memory server, subzero. Use it; never the old "personal-context-mcp" connector.

Sigils say where things belong. Read them in what the user types, and use them yourself:
  %Code/portfolio-site  project      ^cfa2/fixed-income  lesson (topic becomes a concept)
  &tailscale            setup/tool   #comptime           concept    @handle  person
  >claude >codex >grok >gemini >pi >me >crew            who a dispatched job goes to

Working rules:
- Starting real work: the context below is your handoff. Ask subzero for more
  (context_bootstrap or `subzero prompt`) only when it is not enough.
- Finishing real work: save once with session_checkpoint (or note_write), sigils in the title.
- Other sessions can message you; their messages appear in your context as "Messages for you".
  Reply with `subzero msg <session or ^home> "<text>"`; tell the user with `subzero msg me "<text>"`.
- Work for another agent: `subzero dispatch "<goal with sigils>"` (add --ship only when the user
  approved the change). Network, credentials, deletion and deploys always start as scout.
- Lessons (a ^ home): build the scaffolding, but leave the one or two lines that teach the
  idea as `TODO(you): <what to write>`. Then run
  `subzero agent status waiting "Type <what> in <file>:<line>"` and stop. When the user says
  done, read the file, check it, explain what it does, and run `subzero agent status working`.
"""


def ensure_personal_files() -> list[Path]:
    """Copy MEMORY.md and MACHINES.md templates into the config folder if missing."""
    created = []
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name in PERSONAL_FILES:
        target = CONFIG_DIR / name
        if not target.exists():
            target.write_text(resources.files("subzero.templates").joinpath(name).read_text())
            created.append(target)
    return created


def _read(path: Path, limit: int) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(errors="replace").strip()
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n[... trimmed; read the file for the rest]"
    return text or None


def render_package(package: dict[str, Any], limit: int = 2500) -> str | None:
    """A bootstrap package as a few readable lines."""
    lines: list[str] = []
    project = (package.get("project") or {}).get("slug") if package.get("project") else None
    if project:
        lines.append(f"Project: %{project}")
    for item in package.get("items") or []:
        title = item.get("title") or item.get("type", "")
        body = " ".join(str(item.get("content", "")).split())[:400]
        lines.append(f"- [{item.get('layer', '')} {item.get('type', '')}] {title}: {body}")
        for question in item.get("open_questions") or []:
            lines.append(f"    open: {question}")
    for note in package.get("notes") or []:
        if not note.startswith("Sigils read"):
            lines.append(f"({note})")
    text = "\n".join(lines).strip()
    return text[:limit] if text else None


def render_messages(messages: list[dict[str, Any]]) -> str | None:
    if not messages:
        return None
    return "\n".join(f"- from {m.get('from_label', '?')}: {m.get('body', '')}" for m in messages)


def build(
    *,
    cwd: str,
    agents_md: bool = True,
    package: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    home: str | None = None,
) -> str:
    parts = [CORE]
    if session_name:
        where = f" in {home}" if home else ""
        line = f"You are registered as `{session_name}`{where}."
        if session_id:
            short = session_id[:8]
            line += (
                f" Your session id is {short}: add `--session {short}` to "
                "`subzero agent status`, `subzero msg` and `subzero claim`, so they act as you "
                "even when other sessions share this folder."
            )
        parts.append(line)
    if agents_md:
        from subzero.client import repo_root

        root = repo_root(cwd) or Path(cwd)
        if text := _read(root / "AGENTS.md", 2500):
            parts.append(f"## AGENTS.md ({root.name})\n\n{text}")
    for name in PERSONAL_FILES:
        if text := _read(CONFIG_DIR / name, 1500):
            parts.append(text if text.startswith("#") else f"## {name}\n\n{text}")
    if package and (text := render_package(package)):
        parts.append(f"## Where you left off\n\n{text}")
    if text := render_messages(messages or []):
        parts.append(f"## Messages for you\n\n{text}")
    prompt = "\n\n".join(parts)
    if len(prompt) > MAX_CHARS:
        prompt = prompt[:MAX_CHARS].rsplit("\n", 1)[0] + "\n[... trimmed]"
    return prompt
