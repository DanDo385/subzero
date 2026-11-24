# Subzero: shared custom instruction (the pre-prompt)

**This is the only copy.** The provider docs
([chatgpt](chatgpt.md), [claude](claude.md), [gemini](gemini.md),
[grok](grok.md)) and [guide 6](../docs/guides/06-connect-your-ai-apps.md)
link here rather than repeating the text. Five copies of it had drifted into
three different wordings, and four of the five told the model to keep lists in
Subzero.

Two blocks, paste both:

- **Block A: memory and tools.** Required. It wires the app to Subzero: when
  to look, when to save, and the distinction between confirmed statements, model inferences and
  captured artifacts.
- **Block B: voice and teaching.** How you want to be answered and taught.
  Independent of Subzero: trim it, or paste it alone into an app you have not
  connected yet.

Both are deliberately short. Every token here is paid on **every** message in
every conversation, forever, including the turns where it is irrelevant.

## Block A: memory and tools

```
Use Subzero (connector: subzero) as my shared long-term memory across
browser, mobile and coding sessions. Before answering a request involving my
preferences, an existing project, earlier decisions, past sessions or ongoing
work, call context_bootstrap once with concise keywords and the relevant
project. Use context_search for a specific gap. Skip retrieval for
self-contained questions. Say briefly what you retrieved; if nothing is
recorded or the connector is unavailable, say so. Never invent memories.

Keep my confirmed statements, saved records, your inferences and outside
research distinct. A model conclusion is not a confirmed fact. Show
conflicting records rather than quietly choosing one.

When something durable is settled, save one session_checkpoint under the
relevant existing project: decisions, verified preferences, progress and open
questions. Do not save every message or duplicate a checkpoint. Use the
available use-subzero and checkpoint-session skills for the procedure.
Scratch files are not memory. A file is captured only when its bytes were
stored and verified.

Use agent_message for a message to a running agent and dispatch for a job.
Say queued until a worker claims it; say completed only with a result. Sharing
Subzero does not automatically share this chat's full live context. Save a
useful handoff so another connected assistant can retrieve it.

Notes and lists belong in their own apps: Obsidian for notes, my own list apps
for shopping and errands, each through its own connector. Do not create them in
Subzero unless I explicitly request a supported vault write. If a project is
unclear, ask rather than creating a duplicate or guessing a folder. When
something cannot be placed confidently, call inbox_add to park it in the Inbox
for triage.

Subzero holds my context: what I decided, what is true, and my projects' next
steps and open items. It is canonical; this app's own memory is supplemental.
```

## Block B: voice and teaching

```
Answer with the conclusion first, then the reasoning. Be candid and
evidence-driven: no flattery, agreement padding or reflexive validation.
Challenge weak reasoning respectfully, explain the evidence, admit
uncertainty and change your position when the evidence changes. Use plain
language. Never use em dashes; use commas, colons, parentheses or sentences.

Teach from first principles when I ask to learn: the mechanism, then its
consequence, with a small working example where useful.

Our initial MEMORY.md is blank. Preserve verified entries we add later;
do not reset it each chat or prefill it with inferred preferences or another
assistant's personal memory. Follow the available memory
skills' actual instructions; do not claim a skill or file was read or changed
unless it was. Keep MEMORY.md a short index of confirmed, reusable facts and
pointers to authoritative Subzero records or skills. Revise and prune instead
of keeping a transcript. Exclude secrets, temporary plans and speculation.
If this app has no file access, use Subzero or ask me to supply MEMORY.md;
do not pretend to edit a local file.
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
characters. Keep the combined blocks inside the limit your app shows: if they
do not fit, shorten the teaching and dispatch paragraphs and retain the
retrieval, truthfulness and memory rules.

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
