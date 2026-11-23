# Honcho

Honcho (honcho.dev, by Plastic Labs) keeps a model of you as a "peer": it reasons
over what you say across sessions and answers questions about your preferences
and patterns. In this experiment it is the **on-demand reasoning layer**.
`MEMORY.md` stays the hot path that every session loads; Honcho is asked only
when a question needs more than that.

## Connect it

Honcho needs an agent that talks to it. Two ways:

**Hermes Agent** (already supports it): in `~/.hermes/config.yaml`

```yaml
memory:
  provider: honcho
```

with `HONCHO_API_KEY` supplied from your secrets manager, not typed into the
file. Hermes then records every conversation and can answer "what does Honcho
think I prefer about X".

**Any MCP client** (Claude Code, Codex): add Honcho's MCP server with the key
from your secrets manager, for example

```bash
claude mcp add honcho -- npx -y @honcho-ai/mcp
```

Check the current package name and settings at docs.honcho.dev before running
it; the MCP server is newer than the API and its install line changes.

**Subzero** (feeds your history in): point Subzero at the same workspace and
peer, with the key from your secrets manager:

```bash
SUBZERO_HONCHO_WORKSPACE=<the workspace your agent uses>
SUBZERO_HONCHO_PEER=<your peer name there>
SUBZERO_HONCHO_API_KEY=op://<vault>/<item>/<field>
```

Then `subzero memory-lab honcho-sync` sends your own messages from the sessions
Jev marked as carrying a preference, oldest first, so Honcho sees how they
changed. Run it without `--apply` first to see how much would be sent.

## Snapshot it into the vault

Automatic: `subzero memory-lab honcho-snapshot` writes Honcho's card and newest
conclusions to `Experiments/Analysis/honcho-snapshot-YYYY-MM-DD.md`.

By hand, if Subzero is not connected to Honcho:

Honcho's conclusions live in Honcho. For the weekly analysis to compare them with
the other layers, take a snapshot once a week, before the analysis:

1. Ask the agent connected to Honcho:
   *"Ask Honcho what it has concluded about me: preferences, working patterns,
   current projects, and anything that changed in the last week. List each
   conclusion on its own line with how confident it is."*
2. Save the answer as `Experiments/Analysis/honcho-snapshot-YYYY-MM-DD.md`, with
   this frontmatter:

   ```yaml
   ---
   date: YYYY-MM-DD
   layer: honcho
   source: hermes   # or the client you asked
   ---
   ```

The snapshot is read-only evidence. Promote anything from it the same way as
everything else: through the weekly analysis.
