---
name: save-resource
description: Save something the user found (a video, article, paper, repo or talk) as a resource: capture what you can actually see, park it in the Inbox, and leave the next step. Use when they send a link and say "save this", "add this to my resources", or "what should I do with this".
---

Three writes, in this order, and no invented metadata. If you could not fetch
the page, work only with what the user gave you and say what is missing.

## 1. Capture what you actually have

When you hold the metadata (title, author or channel, URL, published date,
length, description), store that text with `artifact_save_text`:
`artifact_type="video"` for a video, `document` for an article or paper,
`code_project` for a repository. The result's `capture_status` tells you what
happened, and only `captured` means the bytes or the full text are stored.

When all you have is a URL, use `artifact_register_reference` instead. That
records that the thing existed, with status `pending_import`. Never call it
saved or archived, and never say you stored the video itself: you did not.

## 2. Park it in the Inbox

Call `inbox_add` with:

- `title`: the resource's own title, not your summary of it
- `content`: two or three sentences on what it is and why it mattered to the
  user, the URL, and the artifact id from step 1
- `why_uncertain`: what is genuinely undecided, for example "resource or
  Permanent note; may belong to the portfolio project"
- `project`: only when the user named one

The Inbox is the single holding area for anything that still needs a person.
Do not guess a folder and do not invent a project to file it under: filing
happens through `review_reclassify` or the weekly analysis.

## 3. Leave the next step

A concrete next step for a project ("watch it and decide whether the duration
model applies to our positions") is a task: `session_checkpoint` with one
`tasks` entry naming that project.

A personal intention ("watch sometime this week") is not a task and Subzero is
not where it lives. Say plainly that it belongs in the user's own list app, and
do not store it here.

## Report

Say what was captured and what was not, the Inbox path (`00-Inbox/...`), that
the vault file is queued rather than written until the local worker runs
(`sync_status` answers whether it landed), and the task if one was stored. Keep
the user's own words distinct from your summary of the resource, and label
anything you looked up yourself as outside research.
