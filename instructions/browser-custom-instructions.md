# Browser and mobile custom instructions

Copy the five-step [Subzero pre-prompt](shared.md) into each connected app.
For a surface without the connector, use the writing and teaching steps.

## Connect Codex and hand context to a browser chat

`/mcp` shows the connections in Codex. To add and authenticate a connection,
run these commands in a shell, replacing the example URL with your server:

```bash
codex mcp add subzero --url https://your-subzero-server.example/mcp
codex mcp login subzero
codex mcp list
```

For a terminal on another computer, `codex mcp login subzero --no-browser`
prints a link you can open on your own computer. Complete sign-in and paste
the final callback URL into the terminal. Keep authorization links and
callback URLs private.

Open or restart a Codex session after setup, then use `/mcp` to confirm that
Subzero is available. See the [official MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Connect the browser or mobile app to the same Subzero account using
[guide 6](../docs/guides/06-connect-your-ai-apps.md).

At the end of CLI work, say:

> Save a Subzero checkpoint for this project: what changed, what was tested,
> the decisions, and what remains unfinished.

In the browser or mobile chat, say:

> Check Subzero and load the handoff for this project, including the latest
> coding session. Tell me what changed and what needs my attention.

MCP connects each assistant to shared records. It does not attach a browser
chat to a terminal's live transcript. The extension's Bridge panel is a
separate session-message feature, and it requires the harness to publish
messages. It does not automatically inject replies into an arbitrary CLI.

The current `subzero hooks install` command installs Claude Code hooks.
Other harnesses can use MCP without those hooks. They need their own
lifecycle adapter for automatic registration and delivery of messages.

Temporary scripts, test databases, and logs go in a managed run under
`~/Code/.scratch/{project}/{run-id}/`, created with `scratch-run`. Code belongs
in the repository, durable session context in Subzero, and requested readable
notes in Obsidian. A temporary test vault inside the managed run is distinct
from your real Obsidian vault.
