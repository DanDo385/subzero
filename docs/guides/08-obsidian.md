# 8. Set up your Obsidian notebook

[Obsidian](https://obsidian.md) is a free notes app that works on plain files in a folder (a
*vault*). subzero fills a vault with clean, linked notes: research, project pages, session
notes, and "map" pages for your topics. You can read them, edit them, and see how they link
together. Shopping lists and errands stay in your own list apps.

The notes are written by **the helper** (`obsidian-sync`), a small program on your computer. It asks
your server "anything new to write?" and follows the server's suggested delay: quickly while work
is arriving, less often when the vault is idle. Nothing on the internet can reach into your
computer: the helper only ever calls out. Older servers use the helper's configured poll interval.

## Step 1: Choose where the vault lives

**Want your notes on your iPhone or iPad too?** (Recommended.) The Obsidian phone app can only open
vaults kept in iCloud Drive's Obsidian folder, which has an awkward address. Make a short shortcut to
it once, so everything else can use `~/Obsidian`:

```bash
ln -s ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents ~/Obsidian
```

> **Tip: use Tab to fill the path.** Type `ln -s ~/Library/Mobile` then press **Tab**, and your terminal
> fills in the rest. If it doesn't, you haven't installed Obsidian on this Mac yet, or iCloud Drive
> isn't set up.

(Obsidian must be installed on the Mac for that iCloud folder to exist. If `~/Obsidian` already exists,
skip the shortcut and, in the next step, use the full address instead:
`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Subzero`.) Your notes are stored in iCloud and show up
in Obsidian on your phone; `~/Obsidian/Subzero` is just an easier way to refer to them.

**Mac only?** Skip the shortcut. `~/Obsidian/Subzero` will then be an ordinary local folder.

Open your `.env.local` and set:

```
OBSIDIAN_SYNC_VAULT_PATH=~/Obsidian/Subzero
```

(the setup wizard already added this line, and the matching `OBSIDIAN_SYNC_API_URL` and
`OBSIDIAN_SYNC_API_KEY`). Create the folder if it doesn't exist:

```bash
mkdir -p ~/Obsidian/"Subzero"
```

Then in Obsidian: **Open folder as vault** and pick that folder.

## Step 2: Test the helper once

```bash
uv run obsidian-sync run --once
```

✅ It exits without errors. If memory-lab triage has already routed notes to the vault, they appear
now. If not, nothing is written yet, and that's fine.

To see what it *would* write without writing anything: `uv run obsidian-sync run --once --dry-run`.

To verify the vault location and server credential without leasing any queued work:
`uv run obsidian-sync check`. It reports `server: reachable and credential accepted` on success.

## Step 3: Keep it running in the background

**Mac:**

```bash
deploy/install-worker.sh
```

✅ It prints `Installed ...` and where its log is. From now on it starts when you log in and restarts
if it ever stops. To stop it for good:
`launchctl bootout gui/$(id -u)/com.subzero.obsidian-sync`.

**Linux (systemd):**

```bash
mkdir -p ~/.config/systemd/user
cp deploy/obsidian-sync.service ~/.config/systemd/user/
cp deploy/obsidian-sync.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now obsidian-sync.timer
```

✅ Check it's running: `systemctl --user status obsidian-sync`. To see recent writes:
`journalctl --user -u obsidian-sync -n 20`.

The timer runs the helper every 5 minutes. To stop: `systemctl --user disable --now obsidian-sync.timer`.
Logs: `journalctl --user -u obsidian-sync -f`.

**Your edits go back too.** When you change a note the helper wrote, the helper sends your version
to the database on its next run, even after a restart or on the Linux timer. It keeps a small record
of what it wrote in `~/.local/state/subzero/obsidian-sync/` (outside the vault, so a vault synced to
another device does not carry it along). A note written before this record existed is tracked from
the next time the server rewrites it. To keep the record somewhere else, set
`OBSIDIAN_SYNC_STATE_DIR`.

## How notes reach the vault

Chat apps no longer write vault notes directly: the note tools were removed in
the cutover, and a checkpoint does not write one either. The vault fills from
what the server queues: session notes routed by memory-lab triage
(`subzero memory-lab recover`), items a chat app parked with `inbox_add`, an
artifact queued with `artifact_queue_for_vault`, and the generated blocks
rendered into notes (a project's `## Next steps`, Related, Maps). "Queued"
means the helper still needs to pick the job up; "written" means it landed.

Each queued note has one home. Existing numbered folders stay in use:

| Logical home | Default vault folder |
| --- | --- |
| `projects/{project}` | `20-Projects/{project}/` |
| `lesson/{category}` | `30-Research/Lessons/{category}/` |
| `setup/{tool}` | `60-Resources/Setup/{tool}/` |
| `other` | `00-Inbox/` |

Concepts are tags such as `concept/lsp`; they do not create extra copies.
Custom root settings replace the numbered root names in this mapping.
Existing files are not moved by this update. If a file already has a different
tracked path, the server refuses a second copy and asks you to relocate it first.

A project's next steps render as a plain-bullet `## Next steps` section inside the note's
generated block, so a re-render refreshes them and your edits to the body are kept. The bullets
are not checkboxes on purpose: ticking one in Obsidian cannot complete the task, and the next
render would silently untick it.

An older list note may still be in your vault from before lists left Subzero. It is inert now:
nothing reads its hidden `sz:id` or `pc:id` markers any more, so ticking a box there does
nothing. Copy anything still open into your own list app, then leave the file as a record or
move it to `99-Archive/`. Ordinary Markdown checkboxes in a prose note are just checkboxes.

(Tip: if `systemctl --user` says "Failed to connect to bus", your Linux setup doesn't have a user
session manager. Use `systemctl` with `sudo` instead: copy the files to `/etc/systemd/system/` and
replace `%h` in the service file with your full home directory path.)

**Windows (WSL):** run `uv run obsidian-sync run` in a terminal you leave open.

## What you'll find in the vault

| Folder | What goes there |
| --- | --- |
| `00-Inbox` | Things worth keeping whose home isn't clear yet, parked by `inbox_add` or Jev triage |
| `20-Projects` | One folder per project: status page with its next steps, saved files |
| `30-Research` | Findings from research conversations, with sources |
| `40-Wiki` | Explanations of how things work |
| `50-Concepts` | Single ideas and terms |
| `60-Resources` | Tools, articles, saved files not tied to a project |
| `70-Atlas` | One session index per assistant, plus map pages that link a topic ([guide 10](10-build-your-wiki.md)) |

The four index notes are `ChatGPT`, `Claude`, `Grok`, and `Gemini`. Each lists the newest
200 sessions: title, date, and the first line of the summary already stored. A row is a link
only when that session already has its own note. Older sessions are one line, "N older".
Gemini rows are takeout days (one row per day in the export). `vault-fill` writes these
from the database. It does not call a model. See [guide 10](10-build-your-wiki.md).

A `## Next steps` section shows the project's open and blocked tasks; done and dropped tasks stay out of the file.
Rendered files carry a `type/` tag in frontmatter so the graph can be filtered:
`type/project` on project notes, `type/moc` on maps, and
`type/session-index` on the four indexes. Tags already stored on a note are kept.

## Editing notes

You can edit any note. The helper notices and sends your change back to your server, so every AI app
sees your version. Two things to know:

- The bottom of a note, between the lines `pc:generated` and `/pc:generated`, is maintained
  automatically (links, maps, sources). Anything you type there is replaced; write above it.
- If you edit a note at the same moment an AI app updates it, your edit wins. The update is held back
  as a *conflict* instead of overwriting you.

Checkboxes you type into a prose note are yours alone; nothing syncs them back to the database.

## Common problems

| You see | Fix |
| --- | --- |
| `Vault directory does not exist` | Create the folder (step 1), or fix `OBSIDIAN_SYNC_VAULT_PATH`. |
| `worker rejected: check OBSIDIAN_SYNC_API_KEY matches` | The helper's key differs from the server's `SUBZERO_WORKER_API_KEY`. They must be identical; after changing the server's, run `deploy/vercel-env.sh .env.local` and `vercel deploy --prod`. |
| Notes never appear | The vault fills from memory-lab triage and `inbox_add` (guide 16), not from asking a chat app to save a note: run `uv run subzero memory-lab recover` after checkpoints pile up. Also check the log: `tail -f ~/Library/Logs/subzero/obsidian-sync.err.log`. |

**Next:** [9. Import your old chats](09-import-your-history.md)
