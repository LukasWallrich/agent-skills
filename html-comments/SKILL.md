---
name: html-comments
description: Add a Google-Docs-style comment + suggested-edit layer to any static HTML page (Quarto reports, plain HTML) deployed anywhere (surge.sh, GitHub Pages). Comments persist in a Google Sheet via an Apps Script endpoint and round-trip between reviewers. Use when the user wants reviewers to comment on / suggest edits to an HTML report, or wants to read collected comments and apply suggestions back into the source (.qmd/.md/.html).
---

# HTML Comments — reviewer comments + suggested edits on static HTML

A self-contained overlay (`assets/html-comments.js` + `assets/html-comments.css`, no
dependencies) that lets readers select text, leave threaded comments or suggested edits,
resolve/reopen threads, and see everyone else's comments. Storage is a Google Apps Script
endpoint of your own (see `gsheet-collect-endpoint`), configured in `config.local.json`.

## ⚠️ FIRST: choose a unique `data-project` slug — this is mandatory, not a detail

The whole system keys on one string: `data-project`. It names the Google Sheet **tab**
every comment on the page reads from and writes to. **The single most important step in
this skill is giving each distinct document its own slug.** Get this wrong and you get
chaos:

- **Reusing a slug across two different documents** (or forgetting to change the
  `UNIQUE-PROJECT-SLUG` placeholder) points both pages at the *same* tab. Each page then
  tries to anchor the other page's comments into text that doesn't exist there, so comments
  render as orphans, land on the wrong passage, or vanish — and the two reviewer groups
  stomp on each other's threads. This is not recoverable by editing; it corrupts the shared
  log.
- **The slug is not auto-checked.** Nothing warns you if a slug is already in use — the tab
  is silently created or silently shared. You are the only guard.

Rules:
1. **One slug ↔ one document.** Never reuse a slug for different content. Derive it from the
   document (e.g. `zcurve-predictive-accuracy`), not from a generic word like `report` or
   `draft`.
2. **New version you want reviewed separately → new slug** (e.g. append `-v2`). Comments do
   not migrate between slugs.
3. **Deploying a second report? It needs its own slug AND its own deploy target/domain.**
   Confirm you are not overwriting an existing deployment or an existing tab.
4. **Before deploying, state the chosen slug to the user** and confirm it is not already in
   use by another live document. When unsure whether a slug exists, `GET
   <endpoint>?action=rows&project=<slug>` (see below) — a non-empty result means the tab is
   already taken.

## Setup: `config.local.json`

This skill needs two URLs of your own, kept out of the repo in `config.local.json`
(gitignored — copy `config.example.json`):

```json
{
  "endpoint": "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec",
  "assetBase": "https://your-asset-host.example/",
  "publishTarget": "your-asset-host.example"
}
```

- **`endpoint`** — your own deployment of the Apps Script collect endpoint (see the
  `gsheet-collect-endpoint` skill / `apps_script/Code.gs`). It is public-by-URL and writes
  into your Google Sheet, so use your own, never someone else's.
- **`assetBase` / `publishTarget`** — a static host you control where `bin/publish.sh` puts
  `html-comments.js` + `.css` (surge.sh works well; so does any host serving
  `cache-control: max-age=0, must-revalidate`).

**Read `config.local.json` and substitute both values wherever this file writes
`<ENDPOINT>` or `<ASSET-BASE>`.** If the file is missing, stop and ask the user to create
it rather than guessing a URL.

## One canonical copy — never copy the assets into a project

`assets/` here is the source of truth, published to `<ASSET-BASE>` by `bin/publish.sh`.
Every page loads the overlay from that URL, so a fix ships to all reports at once — the
next page load picks it up, no report needs re-rendering or re-deploying.

Do **not** copy `html-comments.js` / `.css` next to a report. That is what caused three
different vintages of the overlay to be live at the same time, one of them frozen inside a
rendered HTML file by `embed-resources: true`.

Workflow for a change: edit `assets/`, bump `HC_VERSION`, run `bin/publish.sh`. Check what
is live with `curl -s <ASSET-BASE>html-comments.js | grep HC_VERSION`, and what a page is
running with `window.__hcVersion` in its console.

## Enable on a page

Add before `</body>`, **replacing `UNIQUE-PROJECT-SLUG` with a fresh slug** (see the warning
above — do not ship the placeholder, and do not copy a slug from another report):

```html
<script>
(function () {
  window.HC_CONFIG = {
    endpoint: '<ENDPOINT>',
    project: 'UNIQUE-PROJECT-SLUG'
  };
  var BASE = '<ASSET-BASE>';
  var link = document.createElement('link');
  link.rel = 'stylesheet'; link.href = BASE + 'html-comments.css';
  document.head.appendChild(link);
  var s = document.createElement('script');
  s.src = BASE + 'html-comments.js';
  document.head.appendChild(s);
})();
</script>
```

The overlay is injected from script rather than written as a `<script src>` tag on purpose:
pandoc's `embed-resources: true` inlines referenced assets, which would freeze a copy into
the rendered HTML. A string inside JS is invisible to it, so this snippet works with
`embed-resources` either way. It also works from a `file://` page opened locally — an https
script/stylesheet loads fine from a local file, and the backend needs the network regardless.
(A plain `<script src="https://…" data-endpoint=… data-project=…>` tag still works too; the
script reads `data-*` attributes when present and falls back to `window.HC_CONFIG`.)

For **Quarto**: put the snippet in a `comments-include.html` next to the .qmd and add
`include-after-body: comments-include.html` to the HTML format. Re-rendering keeps the
layer. Note that a `comments-include.html` copied from another project will carry that
project's slug — change it.

The overlay is always on: a floating button bottom-right (badge = open-thread count) opens
the sidebar; reviewers select text to get 💬 Comment / ✏️ Suggest. In the suggestion
composer the original text is pre-filled *and pre-selected*, so typing replaces it the way
it would in Word, while a click or arrow key edits it in place. An identity row at the top
of the sidebar shows who you are posting as and turns amber ("Posting as Anonymous") until a
name is set; replies require a name, as comments already did. A reply gets a **Submit**
button next to its box as soon as there is text, and the thread shows a blue pending stripe
until it is sent — Resolve and Delete sit on a separate row below. Unsubmitted reply text is
also kept in localStorage per thread. Names are remembered in localStorage; no auth — the
endpoint is public-by-URL, so don't use it for sensitive content.

## Working offline

Reviewing on a plane works, with one precondition: **the page must be opened while online**,
because the overlay itself is fetched from `<ASSET-BASE>` and served with
`max-age=0, must-revalidate`, so a reload with no network gets no overlay at all. Leave the
tab open (a discarded background tab reloads from the network on return).

Given that, the overlay keeps two localStorage stores per project — `hc-cache-<project>`
(the last successful server read) and `hc-outbox-<project>` (records not yet accepted) — and
always renders **cache + outbox**. So offline, a reviewer sees the existing comments and
everything they add, across reloads, with a "N pending" chip in the panel header and a
banner explaining the state. The queue is retried on page load, on the `online` event, on
tab re-focus, and when the chip or ↻ is clicked. Authoring time is preserved in the note's
`cts` field, since the server stamps rows at the moment they arrive, not when they were
written.

Records carry unique `itemId`s and the overlay dedups on them, so a post the server accepted
but whose response was lost renders once even though the replay leaves two rows in the sheet.

## Record format (sheet rows)

Standard endpoint columns; `vote` holds the record type (`comment`, `suggestion`, `reply`,
`resolve`, `reopen`, `delete`), `itemId` the record id, and `note` a JSON payload:
`{v, text, kind, replacement?, anchor:{…}, parentId?}`.
Append-only event log; latest resolve/reopen wins; `delete` hides its target.

The anchor carries enough context to place even a one-word suggestion:

| field | meaning |
|---|---|
| `quote` | the selected text |
| `prefix` / `suffix` | up to 120 chars of rendered text either side |
| `containerId` | id of the nearest ancestor with an id |
| `nth` / `total` | which occurrence of `quote` this is within that container, of how many |
| `block` | the whole paragraph/list-item/cell text (≤700 chars), whitespace-collapsed |

Records written before 2026-07-25 have only `quote`, a 30-char `prefix`/`suffix`, and
`containerId`.

## Reading comments programmatically

```
GET <endpoint>?action=rows&project=<slug>
→ {"ok":true,"rows":[{ts,itemId,vote,note,voter,session}, ...]}   (oldest first)
```

(`action=rows` requires web-app deployment @2 or later of the endpoint script.)

## Applying comments/suggestions back to the source (.qmd etc.)

When asked to apply collected feedback:

1. Fetch the rows (curl the GET above; `-L` — Apps Script 302-redirects).
   To POST from curl (e.g. resolve records): use `curl -sL --data '...'` WITHOUT `-X POST` —
   forcing the method re-POSTs to the redirect target and returns a Drive "Page not found".
2. Reduce the log: group by thread root (`parentId`), drop `delete`d records and threads
   whose last resolve/reopen event is `resolve` (unless asked to include resolved).
3. For each open item, locate `anchor.quote` in the **source** file (the quote comes from
   rendered text: strip markdown/inline-code differences; search tolerantly).
   For short or repeated quotes ("a" → "the"), do not match on `quote` alone: find
   `anchor.block` in the source first — it is the whole surrounding paragraph and is
   near-unique — then use `anchor.prefix`/`suffix` to pick the spot inside it, and
   `anchor.nth`/`total` as the final check that you landed on the right occurrence. If the
   context does not pin down a single site, flag it rather than guessing.
   - Plain-prose matches: apply `suggestion` replacements directly; surface `comment`s to
     the user with file:line locations.
   - Quotes that fall inside inline-R output, citations, or generated tables will not match
     the source verbatim — flag these for manual handling instead of guessing.
4. After applying, POST a `resolve` record for each handled thread (same endpoint contract)
   so reviewers see them close, and note the resolution to the user.

## Debug hooks

`window.__hcInjectRows(rows)` renders fake rows through the real pipeline and skips the
server read, so injected rows stay put; `window.__hcState()` exposes internal state
(including the running version) and `window.__hcVersion` the version alone. A missing/old
backend shows a banner instead of failing silently. See "Working offline" above for how
failed POSTs are queued and retried.
