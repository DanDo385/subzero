# subzero

**One shared memory for ChatGPT, Claude, Gemini, Grok and Muse, and a tidy Obsidian notebook built from it.**

Most people who use AI a lot have the same problem. You explain your project to ChatGPT on Monday,
to Claude on Tuesday and to Gemini on Wednesday. Each app remembers a little, none of them remembers
what the others were told, and years of useful conversations sit in separate apps you can't search
together.

subzero fixes that. It's a small server **you** own, and the apps connect to it:

- **Tell one app, and the others can know.** Decisions, preferences, projects and files are saved in
  one place that every connected app reads from and writes to.
- **Your old chats become useful.** Import your chat history, and it's sorted into projects and
  topics automatically. The important parts are pulled out properly.
- **You get a real notebook.** An [Obsidian](https://obsidian.md) vault is filled with clean, linked
  notes and "map" pages for your topics, not thousands of raw transcripts.
- **It's yours.** Your data lives in a database in your own account. Nobody else, including the authors
  of this project, can see it.

It isn't another chat app. You keep using ChatGPT, Claude, Gemini, Grok and Muse, on your phone and
on the web. This sits underneath them. Lists stay in whatever list app you already use.

**Already connected?** Skip to [How to use it](docs/guides/16-using-subzero.md): what to say,
what gets saved, and the skills and tools.

---

## How it works, in one picture

```mermaid
flowchart LR
    subgraph Apps["The apps you already use"]
        A[ChatGPT]
        B[Claude]
        C[Gemini]
        D[Grok]
        E[Muse]
    end
    Apps -->|"connect with a web address<br/>and sign in once"| S["Your subzero server<br/>(free hosting, e.g. Vercel)"]
    S --> DB[("Your database<br/>(free Supabase account)")]
    S -.->|notes| W["A small helper on your computer"]
    W --> O[("Your Obsidian notebook")]
```

1. You put the server online. There are free options, and the guides walk you through every click.
2. You add its web address to each AI app as a "connector". Each app asks you to sign in with Google
   once, and only **your** Google account is allowed in.
3. From then on, the apps save important things as you talk and look them up when they're relevant.
   You can also just ask: *"What did we decide about the kitchen renovation?"*

---

## Is this for me?

**Yes, if** you use two or more of these AI apps regularly, and you're comfortable following a
step-by-step guide that includes copying and pasting a few commands. You don't need to be a
programmer. The guides explain every step, what you should see, and what to do if you see something
else.

**Maybe not yet, if** you only use one AI app. Its built-in memory may be enough.

**What you need:**

| | Why | Cost |
| --- | --- | --- |
| A computer (Mac, Linux or Windows with WSL) | To set things up. After that it can be off. | n/a |
| A Google account | The apps sign in to your server through Google. | Free |
| A [Supabase](https://supabase.com) account | Your database. | Free plan is enough to start |
| A [Vercel](https://vercel.com) account | Puts your server on the internet. | Free (personal use) |
| [Obsidian](https://obsidian.md) *(optional)* | The notebook app. | Free |
| A TypeSafe key *(optional)* | Cheaply sorts years of old chats. | Pay as you go |
| An Anthropic key *(optional)* | Reads your most important old chats closely and writes proper notes. | Pay as you go, with a cost estimate before anything runs |

Plan for **about an hour** the first time, most of it creating the free accounts.
See [Privacy and costs](docs/privacy-and-costs.md) for details.

---

## Get started

Follow the guides in order. Each one ends with a check that tells you it worked.

| Step | Guide | Time | Required? |
| --- | --- | --- | --- |
| 0 | [Before you start](docs/guides/00-before-you-start.md): what you'll end up with, and the few words worth knowing | 5 min | ✅ Read first |
| 1 | [Install the tools](docs/guides/01-install-the-tools.md) | 10 min | ✅ |
| 2 | [Try it on your computer](docs/guides/02-try-it-locally.md) | 10 min | Optional |
| 3 | [Create your Google sign-in](docs/guides/03-google-sign-in.md) | 10 min | ✅ |
| 4 | [Create your database](docs/guides/04-database.md) | 10 min | ✅ |
| 5 | [Put it online](docs/guides/05-put-it-online.md) | 15 min | ✅ |
| 6 | [Connect ChatGPT, Claude, Gemini, Grok and Muse](docs/guides/06-connect-your-ai-apps.md) | 5 min per app | ✅ |
| 7 | [Keep your secrets in 1Password](docs/guides/07-keep-secrets-in-1password.md) | 10 min | Optional |
| 8 | [Set up your Obsidian notebook](docs/guides/08-obsidian.md) | 10 min | Optional |
| 9 | [Import your old chats](docs/guides/09-import-your-history.md) | depends on how many | Optional |
| 10 | [Build your wiki: links and maps](docs/guides/10-build-your-wiki.md) | 10 min | Optional |
| 11 | [Gemini without custom apps](docs/guides/11-gemini-without-custom-apps.md) | 15 min | Only if on Gemini Plus/free |
| 12 | [Daily use: what to expect](docs/guides/12-daily-use.md) | 5 min | Read after connecting |
| 13 | [The browser extension](docs/guides/13-browser-extension.md) | 10 min | Optional: save chats from any app, and backfill old ones |
| 14 | [Run it on your Mac over Tailscale](docs/guides/14-run-on-your-mac-over-tailscale.md) | 15 min | Optional: host it yourself, private to your devices |
| 15 | [Agents, messages and the board](docs/guides/15-agents-messages-and-the-board.md) | 15 min | Optional: coding agents share one prompt, message each other; a live board |
| 17 | [Memory lab](docs/guides/17-memory-lab.md) | 30 min, then weekly | Optional: Jev sorts old sessions into PARA notes and an Inbox; compare memory layers over a few weeks |
| Reference | [How to use it](docs/guides/16-using-subzero.md) | 15 min | Read after connecting: what to say, what gets saved, skills and tools |

The quickest possible start, if you already have the tools:

```bash
git clone https://github.com/DanDo385/subzero.git
cd subzero
uv sync
uv run subzero init      # answers a few questions and writes your settings to .env.local
```

**Where your settings go:** everything personal (addresses, keys, passwords) lives in one file,
`.env.local`, in the project folder. It's never uploaded to GitHub. `init` writes it for you; to
fill it in by hand, start from [`.env.local.example`](.env.local.example).

Stuck? Try [Troubleshooting](docs/troubleshooting.md), then the [FAQ](docs/faq.md). Words you don't
know are explained in the [Glossary](docs/glossary.md).

---

## What the AI apps can do with it

Once connected, each app gets tools like these. It uses them on its own when your
request needs them, and you can also ask for them directly.

| You say... | What happens |
| --- | --- |
| "Remember that I'm vegetarian." | Saved as a preference every connected app can see. |
| "What did we decide about the database last month?" | Searches decisions and past conversations from every app. |
| "Where did we leave the website redesign?" | Pulls that project's status, decisions and open tasks when the name matches. |
| "Search my memory for Neovim." | Searches with a few terms chosen from the topic. |
| "Save this." | Saves the session once: summary, decisions, facts, tasks, open questions. |
| "I'm not sure where this belongs." | Parks it in the vault's Inbox and opens one review question, rather than guessing a folder. |
| "Save this chart to my research project." | Stores the file itself when the app has the bytes. A name alone is only a reference. |
| "Email this to reader@example.com." | Shows a draft, then sends only after you confirm. Needs AgentMail. |
| "Have Claude add dark mode." | Queues a job. It is not started until an agent on your machine picks it up. |
| "What are my agents doing?" | Lists running sessions, open jobs, and messages waiting for you. |

Notes and lists are not kept here. Vault notes come from memory-lab triage of your sessions,
and a shopping list stays in your own list app. What Subzero keeps is context: decisions,
facts, and your projects' next steps.

Paste the [short custom instruction](instructions/shared.md) into each app, and install the
[use-subzero](skills/use-subzero/SKILL.md) skill where the app accepts one. That skill is the
map. Fourteen more folders in [skills/](skills/) cover one job each (`search`, `save`,
`email`, and the rest). There are 15 skills and 43 tools. The tools are what the apps call.
The full walkthrough, including every name, is
[How to use it](docs/guides/16-using-subzero.md). Email setup and the permission prompts
each app shows are in [daily use](docs/guides/12-daily-use.md).

## Coding agents (optional)

Machines you pair share the same memory. From a terminal:

- `subzero dispatch` queues a job. Queued means waiting, not started.
- `subzero msg` leaves a message for a session, a project, or you.
- `subzero board` opens a live view on that computer only (`127.0.0.1`). The page never receives the device token.

Setup, hooks, and sigils for a code folder are in
[Agents, messages and the board](docs/guides/15-agents-messages-and-the-board.md).

---

## Privacy in one paragraph

Your conversations and notes live in **your** Supabase database and **your** Obsidian folder. Only the
Google account(s) you list can connect. Anything that looks like a password, key, ID number or card
number is kept on your computer and never sent to any outside service. You can choose to keep health,
legal and money-related conversations local too. The optional sorting (TypeSafe) and note-writing
(Anthropic) services only receive the text they need, after passwords and emails have been removed.
Full details: [Privacy and costs](docs/privacy-and-costs.md).

---

## For developers

- [Architecture and design](docs/architecture.md): the long-form design document covering sources of
  truth, the memory layers, the classifier and confidence policy, Obsidian sync, and the security model.
- [Deploy scripts](deploy/README.md) and [configuration reference](.env.example).
- [Contributing](CONTRIBUTING.md): running the tests, the database-backed test tier, and the checks
  every change must pass.

```bash
uv sync --all-groups
uv run ruff check src tests && uv run mypy src && uv run pytest
```

## License

[MIT](LICENSE)
