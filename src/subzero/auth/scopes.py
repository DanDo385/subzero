"""Capability scopes.

Every MCP tool declares the scopes it needs. An unmet per-tool requirement is
enforced by visibility: FastMCP filters the tool out of the advertised list
(``_component_auth_allows`` in fastmcp/server/server.py) and a direct call is
refused as an unknown tool ("Unknown tool: '<name>'", isError=true,
fastmcp/server/mixins/mcp_operations.py:321). The spec-correct
``403 insufficient_scope`` challenge (``InsufficientScopeError`` in
fastmcp/server/middleware/authorization.py) fires only for server-global auth
checks, which Subzero does not install: the denial a scope-less client sees is
a hidden tool, not a 403.

HOW SCOPES ARE GRANTED
----------------------
Two paths, chosen by ``SUBZERO_AUTH_MODE``:

* ``jwt`` - an external identity provider mints these scopes into the access
  token's ``scope`` claim. The token is authoritative.
* ``google`` / ``github`` - the upstream provider cannot mint scopes it has
  never heard of, so the token proves *identity only*. Capability scopes are
  then granted server-side from ``SUBZERO_CLIENT_SCOPE_POLICY``, keyed on the
  registered OAuth client's id or on the app its redirect URIs belong to. This is a real, enforced boundary - it is just
  administered by the operator rather than requested by the client.

``agents:exec`` and ``cli:run`` are granted on either path only to clients
the operator named for them: neither is a ``:read`` scope, so the read-only
default for an unmatched client can never include them. ``cli:run`` is the
scope for ``run_cli_on_mbp`` (one command on this Mac, result in the same
chat). Name it for the chat client that should be allowed to ask.

Both paths end with the scopes on ``AccessToken.scopes``, so tool code and
the enforcement machinery do not care which was used.
"""

from __future__ import annotations

CONTEXT_READ = "context:read"
MEMORY_READ = "memory:read"
MEMORY_WRITE = "memory:write"
SESSIONS_READ = "sessions:read"
SESSIONS_WRITE = "sessions:write"
PROJECTS_READ = "projects:read"
PROJECTS_WRITE = "projects:write"
NOTES_READ = "notes:read"
NOTES_WRITE = "notes:write"
ARTIFACTS_READ = "artifacts:read"
ARTIFACTS_WRITE = "artifacts:write"
REVIEW = "review:write"
EMAIL_SEND = "email:send"
AGENTS_READ = "agents:read"
AGENTS_WRITE = "agents:write"
#: Queues commands and interactive sessions for the runner on the user's own
#: machine to execute. This is remote execution, so it is granted by name only:
#: it is not a ``:read`` scope, so DEFAULT_SCOPES can never include it, and the
#: ``"*"`` shorthand deliberately skips it. A policy entry has to say
#: ``agents:exec`` for a client to get it. Name the user's own CLI and harness
#: clients; chat apps should not have it.
AGENTS_EXEC = "agents:exec"
#: Runs one explicit command on the Mac that hosts this server and returns
#: the output to the same chat. Same grant rule as ``agents:exec``: name it
#: in the client policy. ``"*"`` does not include it, so a wildcard cannot
#: hand every connected chat app a shell.
CLI_RUN = "cli:run"

ALL_SCOPES: tuple[str, ...] = (
    CONTEXT_READ,
    MEMORY_READ,
    MEMORY_WRITE,
    SESSIONS_READ,
    SESSIONS_WRITE,
    PROJECTS_READ,
    PROJECTS_WRITE,
    NOTES_READ,
    NOTES_WRITE,
    ARTIFACTS_READ,
    ARTIFACTS_WRITE,
    REVIEW,
    EMAIL_SEND,
    AGENTS_READ,
    AGENTS_WRITE,
    AGENTS_EXEC,
    CLI_RUN,
)

READ_SCOPES: tuple[str, ...] = tuple(s for s in ALL_SCOPES if s.endswith(":read"))

#: Scopes the ``"*"`` shorthand never grants. Queueing a shell command on the
#: user's machine is remote execution, and the quickstart policy shipped in
#: ``.env.local.example`` is ``{"*": ["*"]}``, so a wildcard has to mean
#: "everything except this". Grant it by name.
EXEC_SCOPES: frozenset[str] = frozenset({AGENTS_EXEC, CLI_RUN})

#: What ``"*"`` expands to.
WILDCARD_SCOPES: tuple[str, ...] = tuple(s for s in ALL_SCOPES if s not in EXEC_SCOPES)

#: Granted when a client matches no policy entry and no default is configured.
#: Read-only by default: a newly connected client can retrieve context but
#: cannot mutate anything until the operator says so.
DEFAULT_SCOPES: tuple[str, ...] = READ_SCOPES


def expand(scopes: list[str] | tuple[str, ...]) -> set[str]:
    """Expand policy shorthands.

    ``"*"`` means every scope except :data:`EXEC_SCOPES`; ``"read"`` means every
    ``:read`` scope; anything else must be a literal scope name, so
    ``agents:exec`` and ``cli:run`` are granted only by naming them. Unknown names are dropped
    rather than silently widening access.
    """
    out: set[str] = set()
    for raw in scopes:
        scope = raw.strip()
        if scope == "*":
            out.update(WILDCARD_SCOPES)
        elif scope == "read":
            out.update(READ_SCOPES)
        elif scope in ALL_SCOPES:
            out.add(scope)
    return out


def resolve_client_scopes(
    policy: dict[str, list[str]],
    *,
    client_id: str | None,
    app: str | None,
) -> set[str]:
    """Pick the scope set for a registered OAuth client.

    Matching, most specific first:
      1. exact ``client_id`` (issued by this server at registration)
      2. the client's ``app`` ("chatgpt", "claude", "gemini" or "grok"),
         recognised from its redirect URIs by auth/clients.py. The display
         name the client chose for itself is never used: anyone can register
         a client called "ChatGPT".
      3. the ``"*"`` default
      4. :data:`DEFAULT_SCOPES`
    """
    if client_id and client_id in policy:
        return expand(policy[client_id])

    if app and app in policy:
        return expand(policy[app])

    if "*" in policy:
        return expand(policy["*"])

    return set(DEFAULT_SCOPES)


def provider_from_client(client_name: str | None, client_id: str | None = None) -> str:
    """Best-effort mapping of an OAuth client to a provider id, for audit rows.

    Guesses from names the client chose itself, so it must never grant
    anything; scopes come from :func:`resolve_client_scopes`.

    Returns ``"unknown"`` rather than guessing when nothing matches - an audit
    trail that invents an attribution is worse than one that admits ignorance.
    """
    haystack = " ".join(filter(None, (client_name, client_id))).lower()
    for provider in ("chatgpt", "claude", "gemini", "grok", "codex"):
        if provider in haystack:
            return provider
    if "openai" in haystack:
        return "chatgpt"
    if "anthropic" in haystack:
        return "claude"
    if "google" in haystack:
        return "gemini"
    if "xai" in haystack or "x.ai" in haystack:
        return "grok"
    return "unknown"
