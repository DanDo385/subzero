# AGENTS.md

Guidance for every coding agent working in this repository (Claude Code, Codex, Pi, OpenCode, Grok
and others). `CLAUDE.md` is a symlink to this file, so there is one source of truth: edit this file.

## Documentation is part of every change

This is meant to be a popular open-source project used by non-programmers. `README.md` is the
friendly front door; `docs/guides/00`-`10` are the step-by-step setup path, written for someone who
has barely used a terminal (what to type, what success looks like, what to do when it fails). Any
user-facing change updates the relevant guide, `docs/troubleshooting.md` for new error messages, and
`.env.example` for new settings. Keep personal deployment values (domains, emails, 1Password item
ids) out of committed docs and templates; they live in Git-ignored `deploy/local.env` and
`deploy/*.env.op`. `subzero init` (`setup_wizard.py`) is the supported way to create a
`.env`.

## Commands

```bash
uv sync --all-groups                       # install, including dev group
uv run subzero migrate                     # apply migrations (--status to see pending)
uv run subzero check                       # validate config, DB, migrations, buckets, Jev
uv run subzero serve                       # run the MCP server
docker compose up -d db                    # Postgres 17 + pgvector on :5432

uv run ruff check src tests
uv run ruff format src tests
uv run mypy src                            # packages subzero, obsidian_sync
uv run pytest
uv run pytest tests/test_paths.py::test_name -x   # single test
```

`ruff check src tests`, `mypy src` and `pytest` are all expected to be clean before a change is
done.

### Tests

Two tiers, split by whether a database is needed:

- Unit tests (`test_paths`, `test_archives`, `test_classifier`, `test_auth`, `test_obsidian`,
  `test_importers`, `test_http_security`) need nothing external.
- `test_integration_*` modules carry `pytestmark = requires_db` and **skip** unless
  `SUBZERO_TEST_DATABASE_URL` is set. The `db` fixture drops and recreates the `public` schema on every
  test, and `conftest._test_dsn` aborts the whole run if the database name does not contain
  `test`.

```bash
export SUBZERO_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/subzero_test
uv run pytest
```

`CONTRIBUTING.md` documents a Docker-free Postgres (`pgserver`, pgvector included) for machines
without Homebrew bottles.

### Migrations

Numbered SQL in `migrations/`, applied once inside a transaction and recorded with a content hash.
**Editing an already-applied migration is refused at runtime** — add a new numbered file instead.
`VECTOR(1536)` in `0002` fixes `config.EMBEDDING_DIM`; changing the dimension requires a migration.

## Architecture

An MCP server (FastMCP mounted in Starlette) that is shared, provider-neutral memory for ChatGPT,
Claude, Gemini and Grok. Postgres+pgvector is canonical structured state, object storage holds
artifact bytes, Obsidian is a rendered human layer. `docs/architecture.md` is the long-form design document;
the points below are the ones that span files.

**Request path.** `server.py` builds the FastMCP server (auth provider, rate-limit middleware,
tool registration via `mcp/register_all`) and mounts it in a Starlette app alongside `/worker/*`
and health routes. Its lifespan opens the asyncpg pool and calls `build_services`.

**Service wiring is explicit.** `services.py` constructs every service in dependency order into one
`Services` dataclass, stored in a module global and reached from tool functions with
`get_services()`. There is no DI framework; add new services to that function and dataclass.

**Tools are thin.** `mcp/tools_*.py` only declare the schema (`Annotated[... , Field(description=)]`),
enforce a scope with `auth=require_scopes(...)`, and delegate to a service in
`memory/ sessions/ projects/ notes/ artifacts/ retrieval/ review/` or to `tasks.py`
(project next steps, rendered into vault notes). Business logic and SQL
belong in the service, never in the tool module. Field descriptions are prompt surface, the model
reads them, so they are written as instructions to the caller.

**`models.py` is the MCP boundary.** Pydantic types with `extra="forbid"`; the `Literal` unions
there (`Provenance`, `Sensitivity`, `StorageStatus`, `ArtifactType`, …) are the vocabulary the rest
of the code and the SQL agree on.

**Classification is two-stage and must stay that way.** Deterministic rules
(`classifier/rules.py`) answer anything code can answer — MIME, extension, file tree, hashes,
duplicates. Jev (`classifier/jev.py`, TypeSafe) answers only typed semantic questions and returns
probabilities; it never returns prose, a path or an action. `classifier/policy.py` maps answers to
actions through confidence bands scaled by an action-class multiplier. Every question and threshold
lives in `classifier/questions.py` on purpose — those two files are what a human reviews. When Jev
is unavailable the result is marked `degraded`, the fallback deliberately refuses to guess a project
or write to the vault, and the object is queued for reclassification.

**`session_checkpoint` is the shared write path** (`sessions/service.py`, the largest module). One
transaction, and idempotent through deterministic keys: `(provider, provider_conversation_id)` for
conversations, content hashes for decisions/memories, normalized text per project for tasks,
never through caller bookkeeping. Preserve both properties when changing it.

**The cloud never reaches the Mac.** `obsidian/queue.py` only enqueues jobs (validated path,
rendered content, `expected_sha256`); `src/obsidian_sync/` polls `/worker/*` outbound with a shared
key, **re-validates the path itself**, compares hashes and acks `done` or `conflict`. The worker
does not trust the server — keep the second validation. Vault paths are rejected by both layers
(`obsidian/paths.py`): traversal, absolute paths, dotfiles, symlinks, unknown roots or extensions.

**Artifacts.** Object keys are generated only by `artifacts/keys.py` from structured inputs; no model
or classifier ever supplies a path. `storage_status` must stay honest — only `captured` means bytes
were stored and SHA-256 verified, and `artifact_register_reference` refuses `status="captured"`.
Manifests are generated in Python from stored rows. Provider ZIPs go through `artifacts/archives.py`
guards (zip-slip, entry count, per-entry and total size, compression ratio, streamed and checked
against bytes actually read).

**Privacy routing happens before any external call.** `JevClassifier._ask` and the embedding service
check sensitivity first; content at a level in `SUBZERO_NO_EXTERNAL_SENSITIVITY` never leaves the process,
never auto-promotes to the vault, and gets a shorter signed-URL TTL. Text that does go out is
redacted first.

**Auth is two layers.** Identity (OAuth 2.1 + PKCE against Google/GitHub, or `jwt` mode) plus an
allowlist of subjects; capability scopes (`auth/scopes.py`) granted server-side per client from
`SUBZERO_CLIENT_SCOPE_POLICY`, defaulting to read-only for an unmatched client. `SUBZERO_SERVER_ID` is defence
in depth only — `tests/test_http_security.py` asserts an unauthenticated caller learns nothing with
or without it. Keep that test honest when adding routes.

**Config is environment-only**, `SUBZERO_`-prefixed, in `config.py`, documented in `.env.example`.
The pre-rename `PC_` names are no longer read. `Settings.validate_startup()` reports *every* unsafe
configuration at once and the server refuses to boot on one (`auth_mode=none|static` in prod, plain
HTTP in prod, OAuth without an allowlist, or a stale `PC_` variable still set).

**Sigils are parsed by code, before Jev** (`sigils.py`): `%project`, `^lesson`, `&setup`, `#concept`,
`@person`, `>agent`. Read in `session_checkpoint` and `context_bootstrap`; a `%project`
only names an existing project and never creates one.

**Agents** (`agents/`): live coding sessions, messages between them and dispatched jobs, on device
tokens over `/api/v1/agents|messages|dispatch` and as MCP tools. Dispatch routing is a sigil, else
the Jev `dispatch` question above `DISPATCH_ROUTE_AUTO`, else `crew`; risky goals always start as
`scout`. `dispatch_jobs` also carries executable intent (`kind='command'|'session'|'goal'`,
queued by the `agents:exec` MCP tools `agent_run` and `agent_open`): the server never
executes anything, and the only code in `src/subzero/` that touches a shell is the runner on
the user's machine. `agents/runner.py` (`subzero agent run`) claims over `/api/v1/dispatch/claim`
and acts only by calling `tmux-axi` or `herdr-axi` with argv lists (`--json`), never
`shell=True`, never raw `tmux`/`herdr`, never as root. It re-validates every job's `cwd`
against the runner machine's own `SUBZERO_EXEC_ROOTS` (the server resolved it with
`agents/exec_paths.py`; the laptop does not trust that word), and a job it cannot place is
finished `failed` with the reason, nothing run. A command that ran is `done` with its real
`exit_code` even when non-zero; only a command that could not run (tool missing, refused
`cwd`, timeout, unparseable output) is `failed`. For `kind='session'`, `goal` is a
description and `first_command` is the text the runner types into the session for the human
to review, never executing it, with Enter sent only when the text ends with a newline; for
`kind='command'`, `command` is what runs and `first_command` is NULL (a schema CHECK enforces
both). A `kind='goal'` job is never executed: the runner prints it for a human (optionally
opening the repo with `herdr-axi open`) or submits the text to the one herdr pane holding an
agent, surfacing an `FM_TASK_ID` refusal verbatim rather than retrying.
`agents:exec` is granted by name only: neither `DEFAULT_SCOPES` nor the
`"*"` policy shorthand includes it (see `EXEC_SCOPES`), so a wildcard policy cannot hand a chat
app a shell. Executable jobs skip sigils and Jev (a command is user content, not
classifier input), and a runner's `output` is user content too: stored as a tail capped at
`OUTPUT_TAIL_CHARS`, never sent to the classifier, the embedder or any external call, and
returned only to the caller. The CLI side (`client.py`, `cli_agents.py`, `prompt.py`, `board/`)
runs on each machine: hooks must never fail the harness, and the board serves only 127.0.0.1 and
never sends the device token to the page.

**Embeddings are optional.** `SUBZERO_EMBEDDING_PROVIDER=none` is the default; retrieval falls back to
full-text search alone, so nothing may assume a vector is present. When a provider is set,
`embeddings/refresh.py` fills `embedding` on messages, notes, memories and summaries in the server's background (small batches every
`SUBZERO_EMBEDDING_REFRESH_SECONDS`, `embedding_skipped` marks what policy forbids), and
`retrieval/semantic.py` serves `memory_deep_search` and bootstrap's optional `semantic` layer, the one
read path that makes a model call (one, with a 2 s timeout). Pass vectors as `$n::text::vector` with
`vector_literal`.

**Historical backfill is two passes.** Import screens sensitivity deterministically
(`importers/sensitivity.py`, can only raise a level), then Jev classifies an excerpt (pass 1),
cached by input in `classification_events.metadata.cache_key`. Pass 2 is memory-lab:
`memorylab/recover.py` applies the triage policy to classified sessions and routes them to
layers, and `subzero memory-lab extract` sends the sessions `memorylab/layers.py` picks
(vault and structured layers) to Claude, writing results through `session_checkpoint` plus
`notes.upsert`. `reclassify-conversations` redoes pass 1 from stored messages.

**The wiki graph lives in Postgres** (`note_edges`, `mocs`, `moc_memberships`; `wiki/service.py`).
Links and maps render into a marked generated block at note-render time (`obsidian/render.py`),
and `extract_note_body` strips that block, the frontmatter and the title from human edits before
they are stored. Keep both halves in step.

**Deployment** is Vercel (`vercel.json` framework `python`; `main.py` → `subzero.asgi:app`);
see `docs/guides/05-put-it-online.md` and `deploy/README.md`. The maintainer's own values live in
Git-ignored `deploy/local.env` and `deploy/*.env.op`, and `.1password/project.toml` routes local runs
to their 1Password Environment (`project-env`).

### Consumers outside `src/`

- `skills/` - portable workflow skills. `use-subzero` is the map of when to call the connector. `search`, `save`, and `email` are the named actions.
- `instructions/` — per-provider connector setup plus the shared custom instruction.
- `scripts/` — thin wrappers over `subzero import <provider>` / `reclassify`.

## Notes, scratch, and other agents

The machines on the tailnet share one Subzero server. Name them in
`~/.config/subzero/MACHINES.md` (mbp, imac, ubuntu, and any machine added
later). Postgres is the conversation history. Obsidian is the rendered copy of
the same notes. Chat with the vault through `context_bootstrap`. Do not keep a
second history in a scratch file.

**Temp files.** `scratch-run` is the only place for temp files. A run lives at
`~/Code/.scratch/{project}/{run-id}/` with `tmp/`, `logs/`, `artifacts/`,
`work/`, and `run.json`, and `TMPDIR` points at that run's `tmp/`. Do not
write notes, handoffs, or task lists under `~/Code/scratch`, `/tmp`, `/var`,
or `/private` outside that run. `scratch-gc` deletes only registered runs that
are old enough. Active, pinned, and unknown runs stay.

**Where a note goes.**

- Session notes reach the vault only through memory-lab triage
  (`memorylab/recover.py`, then `memory-lab extract`): a checkpoint never
  writes a vault note by itself. Record the content in the session
  checkpoint; triage decides the layer.
- Something that cannot be placed goes to `00-Inbox/` with `inbox_add` and
  leaves it through `review_reclassify` or the weekly analysis (guide 16).
- A job or a message for another agent goes to the dispatch board
  (`subzero dispatch`, `subzero msg`, `subzero board` on 127.0.0.1). See
  `docs/guides/15-agents-messages-and-the-board.md`. The board never receives
  the device token. Hooks must never fail the harness.
- Lists are not Subzero state. Do not add list tools, list tables, or list
  instructions; the `lists` and `list_items` tables were exported with
  `subzero export-lists` and dropped in migration 0020. Project next steps
  are `tasks` rows (`src/subzero/tasks.py`); shopping lists and errands live
  in the user's own apps.

**Herdr and the browser.** Herdr commands run in the terminal. The browser
extension captures chats and can open a bridge session with a CLI
(`docs/guides/13-browser-extension.md`, `bridge_sessions`). Grok, the other
CLIs, and herdr attach to that browser session only when the person asks.
Worktree commands stay `herdr` commands. Do not drive herdr from the page.
A worker may pass a claimed job into a herdr pane with `herdr pane send-text`,
as guide 15 describes. Tailnet install is `docs/guides/14-run-on-your-mac-over-tailscale.md`.
Vercel remains the public backup when the Mac is asleep.
