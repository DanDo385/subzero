# Subzero Browser Extension

Captures AI chat sessions from Claude, ChatGPT, Gemini, Grok and Muse into your Subzero
database. Setup for people: [guide 13](../docs/guides/13-browser-extension.md).

## Loading unpacked

1. Open `brave://extensions/` or `chrome://extensions/` (any Chromium browser; not Firefox).
2. Enable **Developer mode**, click **Load unpacked**, select this `extension/` folder.
3. After pulling changes: press the reload arrow on the extension's card.

## How a conversation reaches the database

1. `content.js` (in each provider tab) asks the provider module for the open conversation:
   after a reply finishes, on **Save Now**, or during an import.
2. It hands the capture to the service worker and waits until it is persisted in the queue.
3. `lib/queue.js` keeps one entry per conversation in `chrome.storage.local` and sends them,
   one at a time, to the **paired server only** (`POST /api/v1/ingest/conversation`).
4. Only the server's answer (`conversation_id`, `messages_inserted`) marks it delivered; the
   popup shows that confirmation per provider.

Pairing stores the server address and its device token together. There is no fallback to
another address: a token belongs to one server and one database, and a silent fallback would
send conversations somewhere else.

Tabs that were open before the extension was installed or reloaded have no content script;
`lib/tabs.js` pings the tab and injects the packaged scripts when nothing answers. A newly
injected copy takes over from an orphaned one.

## Delivery rules

- Retries back off from 30 seconds to an hour, up to 8 attempts.
- A 4xx other than 401/408/429 is permanent: the item moves to the failed list at once.
- 401 means the token is unknown: delivery pauses, nothing is dropped, the popup asks to pair.
- Failed items are kept; **Retry** in the popup puts them back in the queue.
- The server deduplicates by provider message id, so sending a conversation again only adds
  new messages.

## Providers

| Provider | Read from | History import | Notes |
|----------|-----------|----------------|-------|
| Claude   | internal JSON API | yes, paged | temporary chats skipped |
| ChatGPT  | internal JSON API | yes, paged | hidden system/context messages skipped; token refreshed on 401 |
| Gemini   | page DOM | open conversation only | selectors can break when the site changes |
| Grok     | page DOM | open conversation only | selectors can break when the site changes |
| Muse     | page DOM | open conversation only | selectors not yet verified against the live site |

Imports run inside the provider tab (about one conversation per second, backing off on 429),
skip conversations whose update time matches what the server already confirmed, and write
progress to `backfill:<provider>` in storage for the popup.

## Permissions

- `storage`, `unlimitedStorage`: queue, settings, device token (large imports exceed 10 MB)
- `scripting`: reconnect capture scripts in tabs opened before installation
- `alarms`: delivery retries and periodic capture
- `sidePanel`: handoff and session bridge
- Hosts: the five provider sites. The server's address is an optional permission requested
  when you pair, so no server address is built into the extension.

## Scripts

```bash
npm test          # unit tests (node --test)
npm run build     # copy the extension into dist/
npm run package   # build + zip
```
