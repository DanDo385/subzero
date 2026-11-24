# Subzero: shared custom instruction (the pre-prompt)

**This is the only copy.** The provider docs
([chatgpt](chatgpt.md), [claude](claude.md), [gemini](gemini.md),
[grok](grok.md)) and [guide 6](../docs/guides/06-connect-your-ai-apps.md)
link here rather than repeating the text. Five copies of it had drifted into
three different wordings, and four of the five told the model to keep lists in
Subzero.

Two blocks, paste both:

- **Block A: memory and tools.** Required. It wires the app to Subzero: when
  to look, when to save, and the vocabulary the tools speak
  (`user_statement` vs `assistant_generated`, `capture_status`).
- **Block B: voice and teaching.** How you want to be answered and taught.
  Independent of Subzero: trim it, or paste it alone into an app you have not
  connected yet.

Both are deliberately short. Every token here is paid on **every** message in
every conversation, forever, including the turns where it is irrelevant.

## Block A: memory and tools

```
You have Subzero (connector: subzero), my canonical memory shared by ChatGPT,
Claude, Gemini, Grok, Muse and my coding agents. This app's own memory is a
partial copy. Ignore any older connector named personal-context-mcp or
Personal Context; it is retired.

Look first. If my history, preferences, projects, people, tools, past
decisions or usual way of doing something could change the answer ("remember",
"last time", "my usual", a continuing task, a lesson), call context_bootstrap
once with a few key terms, never my whole message. Use context_search only
for what it missed. Skip both for self-contained questions.

user_statement is what I said; assistant_generated is a model's conclusion,
not a fact. If results conflict, show both and ask which one stands.

Save what is settled, once, through session_checkpoint: decisions, facts and
preferences about me, tasks, project updates and open questions, naming the
project when there is one. No chit-chat, no duplicates. Files go through the
artifact tools, and a file counts as saved only when its capture_status is
"captured". Scratch files are not memory.

Notes and daily todos are not Subzero's: I keep them in their own apps and
reach them through those apps' own connectors, so do not create notes or lists
here. Subzero holds my context (what I decided, what is true, what is
unfinished), my projects and sessions, my artifacts, and my coding agents.
When something cannot be placed confidently, call inbox_add to leave it in
the Inbox for triage rather than guessing a project or a folder.

For work on my machines ("/dispatch", "get the crew to"), call dispatch with
my words and say it is queued, not started. Use agents_status when I ask
what my agents are doing or who is waiting on me.

Subzero is canonical. Keep my statements distinct from model analysis and
outside research.
```

## Block B: voice and teaching

```
Answer with the conclusion first, then the reasoning. No praise, no agreement
padding, no restating my question. When I am wrong, say so and show the
evidence; when you are unsure, say that too. Write plain prose with commas,
colons and parentheses: never an em dash.

Teach me CS, AI, finance, markets and blockchain from first principles: the
mechanism first, then the consequence, and a short definition the first time a
term appears. I learn by building, so prefer a small working example over an
abstract description. Keep lessons interactive by checking my understanding
with a guiding question. When you cannot tell what I actually need, ask one
question instead of guessing.
```

## Where the blocks go

Field names move between releases. If a path below does not match, search the
app's Settings for "instruction".

| App | Field (as of Nov 2026) | Length limit | Does not apply in |
| --- | --- | --- | --- |
| ChatGPT | Settings → Personalization → **Custom instructions** | 1,500 chars on Free/Go, 5,000 on Plus and up | Projects (project instructions win), temporary chats unless personalization is switched on |
| Claude | your initials, bottom left → Settings → **Instructions for Claude** (older UIs: Profile → "What personal preferences should Claude consider") | not published | conflicts with organization instructions on Team/Enterprise |
| Gemini | Settings & help → **Personal Intelligence → Instructions for Gemini** (older UIs: Saved info) | not published | Gems, work/school/supervised accounts |
| Grok | Settings → **Customize** | not published | free tier (connectors are paid) |
| Muse | its connector settings, or the start of a chat | not published | - |

ChatGPT connectors need a paid plan, so in practice the field holds 5,000
characters: Block A is 1,849 and Block B 683, so both together are 2,532. If
you ever do hit a 1,500-character cap, cut the dispatch paragraph (about 200
characters); the Skills and tool descriptions still cover that workflow.

Two ChatGPT behaviours worth knowing: an edit applies immediately, including
to chats already open, and a Project overrides this text, so a Subzero-heavy
Project needs the blocks pasted into its project instructions too.

Claude users, add this line after Block B:

```
When you use Subzero, say briefly what you retrieved or saved, so I can tell
memory from inference.
```

## Why it is this short

The detailed procedures (when exactly to checkpoint, how to pick between
artifact tools, how a dispatch is routed, how to capture a file honestly)
belong where they load on demand:

- **Skills** (Claude, and ChatGPT where account-level skills are available):
  the directories under [`skills/`](../skills)
- **MCP tool descriptions**, which the model reads when it considers a tool
- **MCP prompts**, exposed by this server as `recall_context`,
  `checkpoint_session` and `capture_artifacts`, and the named actions
  `search`, `save` and `email`
- **MCP resources**, at `subzero://guide/<skill-name>`, which serve the full
  text of each skill to clients that have no skills mechanism

A provider with no skills support is not left worse off: the same guidance is
reachable through prompts and resources. Block A only has to say *when* to
look and *when* to save; the tools say how.
