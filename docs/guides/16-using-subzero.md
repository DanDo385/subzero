# 16. How to use Subzero

The server is online and the apps are connected. This page is the manual for
day-to-day use: what to say, what gets saved, and the names of the skills and
tools. You do not need to memorize the names. The apps pick a tool when your
words call for it. The list is here so you can ask for one on purpose, and so
you can see what is possible.

Setup stays in [guides 0 through 11](../README.md). What each app asks before
a write, plus the purple-giraffe test, is [guide 12](12-daily-use.md). The
browser extension is [guide 13](13-browser-extension.md). Coding agents, the
board, and pairing a machine are [guide 15](15-agents-messages-and-the-board.md).

For two copyable custom-instruction blocks and the CLI-to-browser handoff,
see [browser and mobile custom instructions](../../instructions/browser-custom-instructions.md).

## If you only read one section

1. Start a **new** chat. Chats from before you connected do not know about Subzero.
2. Confirm the short custom instruction is pasted. The text lives in
   [guide 6](06-connect-your-ai-apps.md#the-custom-instruction-same-for-every-app).
   It is the same for ChatGPT, Claude, Gemini, Grok, and Muse. It is paid on
   every message, so it stays short.
3. Where the app accepts a skill, install
   [use-subzero](../../skills/use-subzero/SKILL.md). That file is the procedure.
   An app without a skills folder can open the prompt `use_subzero`, or the
   resource `subzero://guide/use-subzero`.
4. In one app: *"Remember that my test phrase is purple giraffe."*
   In a different app, new chat: *"What's my test phrase? Check Subzero."*
   If the second app finds it, shared memory is working.

Remove a connector still named **personal-context-mcp**. Old memories stay in
your database. They do not live in the connector.

## A normal day

You talk the way you already talk. When a question depends on something from
before (a project, a decision, a preference, "last time", "we decided"), the
app calls `context_bootstrap` once. It sends a few key terms, not your whole
message. The package comes back in this order:

1. The project's handoff: recent summaries, open questions, open tasks, active decisions.
2. Items that match the terms.
3. Raw transcript lines, only if nothing else matched.

The reply should say what it found, and which app it came from. If nothing is
recorded, it should say so. It should not invent a memory.

You do not checkpoint every message. A checkpoint is for something a future
chat would otherwise have to be told again: a decision, a confirmed fact, a
task, a project change, an open question. Small talk, a finished lookup, and
a draft you did not accept stay in the chat.

## What to say

These phrases are optional. They make the action obvious.

| You say | What should happen |
| --- | --- |
| "Check Subzero." | Search what every connected app has saved. |
| "What did we decide about the website redesign?" | Search decisions, including ones made in a different app. |
| "Where did we leave the website redesign?" | The project's status, decisions, and open tasks, when the name matches. |
| "Remember that I prefer dark mode." | Save that preference for every connected app. |
| "Save this." | One checkpoint: summary, decisions, facts, tasks, open questions. |
| "Search my memory for Neovim." | Search with a few terms taken from the topic. |
| "Find that old chat about API pricing." | Search past conversations by meaning. Without embeddings, it matches words and says so. |
| "Send this to my vault." | Queue a note for Obsidian. "Queued" means the helper has not written it yet. |
| "Save this chart to the website redesign." | Store the file when the app actually has the bytes. A filename alone is only a reference. |
| "Email this to reader@example.com." | Show a draft. Send only after you confirm. Needs [AgentMail](12-daily-use.md#optional-email-setup). |
| "Have Claude add dark mode." | Queue a job. It is not started until an agent on your machine picks it up. |
| "What are my agents doing?" | Running sessions, open jobs, and messages waiting for you. |

A shopping list stays in your list app. Asking Subzero to add an item does not
update that app.

## Where a result goes

| Kind of thing | Where it goes | What to expect back |
| --- | --- | --- |
| An answer from past work | Read only. Nothing is written. | What was found, and which app it came from. |
| Something this chat should remember | `session_checkpoint`, once. | What was created, updated, or skipped. |
| A note you will reread | The vault, through `note_write`. | "Queued" or "already written". |
| Work for another agent | `dispatch`. | "Queued", the agent name, and scout or ship. Not "started" or "done". |
| A message for a running agent | `agent_message`. | "Sent." The agent sees it on its next turn. |
| A message for you | `agent_message` with `to` set to `me`, or `subzero msg me`. | It shows on the board and in `subzero inbox`. |
| A file you want kept | The artifact tools, when the app holds the bytes. | "Captured" only if the bytes were stored and checked. |
| A list | Your list app. | Subzero does not keep it. |
| Scratch from an agent run | The temporary folder `scratch-run` creates under `~/Code/.scratch`. | Not memory. Do not look for it later as a note. |

Postgres holds the conversation history and the structured record (projects,
decisions, memories, notes). Obsidian is the rendered copy of the notes. Chat
with that record through the connector. Do not keep a second history in a
scratch file.

## How a project gets chosen

You can name a project, or let the server match one.

**You name it.** Pass the project's slug or name, or type a project sigil
(the next section). A `%` name only points at a project that already exists.
It does not create one. An unknown name is reported, not invented.

**The server matches words.** With no name, it tries, in order: the exact
slug, name, or alias; a repository path in the text; the name written loosely
("sub zero"); a close misspelling; then one distinctive word. A clear winner
is used, and the result says which project and why. A close tie is not
broken: you get the candidates and can pick. Words that appear in almost
every project name ("app", "notes", "server") do not pick a project on their
own.

That match is enough for reading and saving when one project is obviously the
subject. It is the wrong tool when the word is ambiguous. "Claude" can be the
app, a topic, or the agent who should do the work. A sigil is the exact
address for those cases.

## Sigils

A sigil is a symbol at the start of a word. Code reads it. The model does not
guess. You can skip them in ordinary chat and rely on the name match above.

| You type | It means | It files as |
| --- | --- | --- |
| `%portfolio-site` | an existing project | home `projects/portfolio-site` |
| `^cfa2/fixed-income` | a lesson | home `lesson/cfa2`, concept `fixed-income` |
| `&neovim` | a tool or setup | home `setup/neovim` |
| `#lsp` | a concept tag | tag `concept/lsp` |
| `@morgan` | a person | tag `person/morgan` |
| `>claude` | who should do the work | that agent |

The first `%`, `^`, or `&` is the home. `#` and `@` are extra tags, and you
can type more than one. `>` names the agent for a job.

A sigil counts only at the start of a word. These are left alone: `C#`,
`x^2`, `50%`, `a@b.com`, and a URL. Quotes do not start a sigil, so code like
`printf("%s")` is not a project.

Known dispatch targets are `claude`, `codex`, `grok`, `gemini`, `pi`, `me`,
and `crew`. With no `>` in the goal, a confident classification can pick the
agent. Otherwise the job goes to `crew`. Anything that touches the network,
credentials, deletion, or a deploy starts as **scout** (read, plan, report)
even if you asked to ship it.

**On your computer, you do not type them.** A one-line file named `.subzero`
at the top of a repo supplies the home for coding sessions started in that
folder:

```bash
echo "%portfolio-site" > ~/Code/portfolio-site/.subzero
echo "^cfa2/fixed-income" > ~/Code/cfa2/.subzero
```

The nearest `.subzero` above the current folder wins. ChatGPT, Claude,
Gemini, Grok, and Muse do not see that file. In those apps you type the sigil
when the name match is not enough, or you say the project name and let the
server match it.

## Skills, prompts, and tools

Skills, prompts and tools are different surfaces for the same workflow.

| Thing | How many | What it is |
| --- | --- | --- |
| Skill | Folders in [skills/](../../skills/) | Instructions an app loads when the job matches. Install `use-subzero` first. The others are optional. |
| Prompt | 9 | The same kind of instructions, asked for by name inside the connector. |
| Tool | The server lists the current set | A named action. The app calls these. You rarely type the name. |
| Resource | one per skill | `subzero://guide/<skill-name>` returns that skill's file. |

Skill folder names use hyphens (`use-subzero`). Prompt names use underscores
(`use_subzero`). The resource uses the folder name.

A prompt exists for `search`, `save`, `email`, `vault`, `use_subzero`,
`recall_context`, `checkpoint_session`, `capture_artifacts`, and
`manage_lists`. The other skills are files and resources only:
`project-artifacts`, `research-to-obsidian`, `update-project`, `daily-review`.

### The workflow skills

| Skill | Use it when |
| --- | --- |
| `use-subzero` | Any past history, preference, project, decision, workflow, vault note, or job for another agent. Also when the app would otherwise say it has no memory of earlier chats. |
| `search` | "Search my memory for X." |
| `save` | "Save this" or "remember this session." |
| `vault` | "Send this to my vault" or "put this in Obsidian." |
| `email` | Draft a message. Send only after you confirm. |
| `recall-context` | The full retrieval steps before answering from past work. |
| `checkpoint-session` | What belongs in one checkpoint, and what to leave out. |
| `session-capture` | Save a useful session summary and decisions rather than inventing a full transcript. |
| `capture-artifacts` | Store a file, and tell the truth about whether the bytes were captured. |
| `project-artifacts` | Several files that belong together as one versioned artifact. |
| `research-to-obsidian` | Research worth rereading becomes a vault note, with sources. |
| `update-project` | Status, goals, decisions, and the activity log of a project. |
| `daily-review` | Open tasks, unresolved questions, and whether the vault helper is keeping up. |
| `manage-lists` | Say that a list app manages lists. Do not call the old list tools. |

`use-subzero` is the map. Install that one. Add another only when you want
the app to follow that job's longer procedure.

## The registered tools

Grouped the way the server registers them. Read-only tools do not change
your data. A write asks the app for permission the first time. See
[guide 12](12-daily-use.md#approvals-what-to-expect) for how each app asks.

### Context (5, read)

| Tool | What it does |
| --- | --- |
| `context_bootstrap` | The one call that starts a chat continuing earlier work. |
| `context_search` | Ranked search over decisions, memories, summaries, notes, and artifacts. |
| `context_recent` | Newest session summaries. For catch-up, not for one topic. |
| `context_for_topic` | A sized package about one topic. |
| `memory_deep_search` | Search every saved conversation by meaning, then by words. |

`memory_deep_search` uses embeddings when `SUBZERO_EMBEDDING_PROVIDER` is set.
The default is `none`, and search then matches words. Guide 12's
[search by meaning](12-daily-use.md#search-by-meaning-optional) section is the
setup.

### Memory (5)

| Tool | What it does |
| --- | --- |
| `memory_save` | Save one durable fact. |
| `memory_search` | Search saved facts, not whole conversations. |
| `memory_history` | Every version of one fact, newest first. |
| `session_checkpoint` | The shared write: summary, decisions, facts, tasks, project updates, open questions, in one transaction. |
| `session_recent` | Recent summaries and their open questions. |

Send one checkpoint, not several. Repeat calls with the same conversation id
update one record. A content hash keeps a decision or fact from being stored
twice.

### Projects and notes (9)

| Tool | What it does |
| --- | --- |
| `project_list` | Your projects, most recently active first. |
| `project_search` | Find a project by name before creating one. |
| `project_get` | Goals, live decisions, open tasks, recent activity. |
| `project_create` | Create a project. Search first, so one effort does not become two. |
| `project_update` | Change status, goals, or description. Omitted fields stay as they are. |
| `project_event_add` | Append one line to the project's log. An identical line is not repeated. |
| `note_search` | Search notes. |
| `note_get` | Read one note. |
| `note_write` | Write or update a note and queue it for the vault. |

A note has one home: `projects/{slug}`, `lesson/{category}`, `setup/{tool}`,
or `other`. Concepts are tags (`concept/lsp`). They do not make extra copies.
The server builds the file path. You do not supply one. Folders and the
helper are [guide 8](08-obsidian.md).

### Artifacts (14)

An artifact is a file: a chart, a document, a page, a small project tree.

| Tool | What it does |
| --- | --- |
| `artifact_save_text` | One text or code file you actually hold. |
| `artifact_save_bundle` | Several files as one artifact, paths kept. |
| `artifact_save_binary` | A small binary you actually hold. |
| `artifact_begin_upload` | Start a large upload. |
| `artifact_complete_upload` | Finish that upload after the bytes are sent. |
| `artifact_register_reference` | Record that a file existed when this app did not hand over the bytes. Status is not "captured". |
| `artifact_search` | Find artifacts. |
| `artifact_get` | Read one artifact's record. |
| `artifact_manifest` | The list of files that were really stored, with hashes. |
| `artifact_signed_download` | A temporary link to the bytes. |
| `artifact_create_revision` | A new version of an artifact that already exists. |
| `artifact_attach_to_project` | File it on a project's page. |
| `artifact_queue_for_vault` | Queue a vault copy. Large media stays in storage with a link. |
| `artifact_classify` | Ask how an artifact should be filed. |

Say "archived" or "saved" only when the result says the bytes were captured
and the hash matched. Most chat apps show you an upload and do not pass the
file to the connector. In that case the honest result is a reference, filled
in later when you import that app's export.

### Review (6)

| Tool | What it does |
| --- | --- |
| `review_list` | Items the classifier would not guess at. Least confident first. |
| `review_get` | One item, with the classifier's answers. |
| `review_approve` | Accept the suggestion. |
| `review_reclassify` | Send it to a different project or destination. |
| `review_dismiss` | Close it with no change. |
| `sync_status` | Whether the Obsidian helper is keeping up. |

`sync_status` is the check when a note stays "queued". If the helper has not
run, the file is not in the vault yet. Your computer has to be on for the
helper. The apps can still read and write the database while it sleeps.

### Agents (3)

| Tool | What it does |
| --- | --- |
| `dispatch` | Queue a job. Tell the person it is queued, and for whom. |
| `agent_message` | Send text to a session, to a home, or to `me`. |
| `agents_status` | Live sessions, open jobs, messages waiting for you. |

Details of pairing, hooks, and the board are guide 15. The practical rules:

- `agents_status` lists sessions that are not finished, jobs that are queued
  or claimed, and your inbox. It does not start anything.
- `agent_message` writes the message and returns. It does not appear in the
  middle of a turn the agent has already started. With hooks installed, the
  session receives it at the next prompt, under "Messages for you".
- A home such as `^cfa2` or `%portfolio-site` reaches sessions working in that
  home. A message sent before a session existed is not replayed when that
  session later starts.
- A session name that matches two live sessions is refused. The reply lists
  the ids. Use one id.
- `subzero agent status waiting` is different. That is the session itself
  raising a "your turn" sign, for example when a lesson wants you to type the
  important lines. It is not the same as asking `agents_status`.

Jobs stay queued until something on your machine claims them. Queueing from
a phone does not start the work. Scout is the default. `--ship` (or `mode`
`ship`) is for when you have said to make the change, and risky jobs still
start as scout.

### Email (1)

`email` drafts exactly the message you asked for, shows the sender,
recipients, subject, body, and any send time, and sends only after you
confirm. Setup, dry-run, and the scope `email:send` are in
[guide 12](12-daily-use.md#optional-email-setup). A scheduled receipt means
AgentMail accepted it for later. It does not mean the message has arrived.

### Lists (10, frozen)

These tools are still registered: `list_search`, `list_get`, `list_create`,
`list_add_item`, `list_update_item`, `list_complete_item`, `list_move_item`,
`list_archive_item`, `list_recent`, `list_find_item`.

Do not use them. A list app connects and manages lists. Do not ask the model
to write a Markdown list and call that the list. The `manage-lists` skill and
the `manage_lists` prompt both say the same thing.

## How to read what comes back

Every saved item has a provenance. Trust them differently.

| Provenance | Meaning |
| --- | --- |
| `user_statement` | You said it. Treat it as fact. |
| `assistant_generated` | A model concluded it. Call it a hypothesis. |
| `external_source` | Research. Cite it as such. |
| `imported_metadata` | Filled in by an import. The weakest of the four. |

If two items conflict, you should see both, with dates. A decision marked
`superseded` has been replaced. The app should not silently keep the newer
one and hide the older.

Sensitivity is `normal`, `private`, or `highly_sensitive`. Highly sensitive
text (passwords, keys, card numbers, ID numbers) stays in your database. It
is not sent to a sorting or embedding service, and it is not promoted into
the vault. You can widen that with `SUBZERO_NO_EXTERNAL_SENSITIVITY`. Details
are in [Privacy and costs](../privacy-and-costs.md).

## What it will not do

- It will not keep a shopping list, a reading list, or a checklist. Those stay
  in a list app.
- It will not treat a scratch file, `/tmp`, or a note on one machine as shared
  memory.
- It will not invent a memory when the search is empty.
- It will not say a file was archived when only its name was recorded.
- It will not start a dispatched job. Queued means waiting.
- It will not drive herdr, your editor, or your browser unless you ask a
  coding agent to do a specific thing, and that agent is actually running.
- It will not create a project because a `%` sigil mentioned one.

## From the terminal

The chat apps are enough for search, save, notes, and queueing a job. These
commands are for a machine you have paired ([guide 15](15-agents-messages-and-the-board.md),
step 1). Run them from the project folder.

| Command | What you get |
| --- | --- |
| `uv run subzero check` | Config, database, migrations, storage, and the classifier. |
| `uv run subzero migrate --status` | Which migrations are applied and which are pending. It does not write. |
| `subzero agents` | Running sessions, open jobs, messages for you. |
| `subzero inbox` | Messages addressed to you. |
| `subzero msg ^cfa2 "use the new syntax"` | A message to every session in that home. |
| `subzero msg me "ready for review"` | A message on your board. |
| `subzero dispatch "%portfolio-site add a dark mode toggle >claude"` | A queued job. |
| `subzero agent status waiting "your line here"` | This session's own your-turn sign. |
| `subzero board` | The live board at `http://127.0.0.1:7777`. It never receives the device token. |

`subzero agents` answers "who is working". `subzero agent status` changes the
status of the session in the current folder. The board's `?` key lists its
own keys (`d` to dispatch, `m` to message, `/` for the command bar).

Importing old chats, reclassifying, and building the wiki are
[guide 9](09-import-your-history.md) and [guide 10](10-build-your-wiki.md).
They are not part of an ordinary chat.

## When something is wrong

| You notice | What to check |
| --- | --- |
| The app never calls Subzero | The custom instruction, then ask "Check Subzero." [Guide 12](12-daily-use.md#when-something-doesnt-feel-right). |
| A save is missing in the other app | Wait a moment and ask again. Then `uv run subzero check`. |
| A note stays queued | `sync_status`, and whether the helper on your computer is running. [Guide 8](08-obsidian.md). |
| "Not connected" from the CLI | [Guide 15](15-agents-messages-and-the-board.md#when-something-goes-wrong). The machine needs `subzero connect`. |
| Agents list is empty | Hooks are not installed, or this machine was never paired with `--agents`. |
| A job sits queued | Nothing has claimed it. Queueing does not start the work. |
| Gemini stopped connecting | Keep Activity must stay on. [Guide 6](06-connect-your-ai-apps.md). |

More messages: [Troubleshooting](../troubleshooting.md). Short answers:
[FAQ](../faq.md).

**Next:** [12. Daily use](12-daily-use.md) for permission prompts and email,
[13. The browser extension](13-browser-extension.md), or
[15. Agents, messages and the board](15-agents-messages-and-the-board.md).
