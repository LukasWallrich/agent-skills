---
name: html-comments
description: Add a Google-Docs-style comment + suggested-edit layer to any static HTML page (Quarto reports, plain HTML) deployed anywhere (surge.sh, GitHub Pages). Comments persist in a Google Sheet via an Apps Script endpoint and round-trip between reviewers. Use when the user wants reviewers to comment on / suggest edits to an HTML report, or wants to read collected comments and apply suggestions back into the source (.qmd/.md/.html).
---

# HTML Comments — reviewer comments + suggested edits on static HTML

A self-contained overlay (`assets/html-comments.js` + `assets/html-comments.css`, no
dependencies) that lets readers select text, leave threaded comments or suggested edits,
resolve/reopen threads, and see everyone else's comments. Storage is a Google Apps Script
endpoint of your own (source + deploy recipe in `apps_script/`), configured in
`~/.claude/html-comments.config.json`.

## ⚠️ FIRST: choose a unique `data-project` slug — this is mandatory, not a detail

The whole system keys on one string: the project slug. It names the Google Sheet **tab**
every comment on the page reads from and writes to. **The single most important step in
this skill is giving each distinct document its own slug.** Get this wrong and you get
chaos:

- **Reusing a slug across two different documents** (or forgetting to change the
  `UNIQUE-PROJECT-SLUG` placeholder) points both pages at the *same* tab. Each page then
  tries to anchor the other page's comments into text that doesn't exist there, so comments
  render as orphans, land on the wrong passage, or vanish — and the two reviewer groups
  stomp on each other's threads. This is not recoverable by editing; it corrupts the shared
  log. (The overlay refuses to start on the literal placeholder `UNIQUE-PROJECT-SLUG` and
  shows a red banner — but it cannot detect a *reused* real slug.)
- **The slug is not auto-checked for reuse.** Nothing warns you if a slug is already in
  use — the tab is silently created or silently shared. You are the only guard.

Rules:
1. **One slug ↔ one document.** Never reuse a slug for different content. Derive it from the
   document (e.g. `zcurve-predictive-accuracy`), not from a generic word like `report` or
   `draft`.
2. **New version you want reviewed separately → new slug** (e.g. append `-v2`). Comments do
   not migrate between slugs.
3. **Stick to `[a-z0-9-]` and ≤90 chars.** The endpoint sanitises `\ / ? * [ ] :` out of
   tab names on write; a slug containing them writes to one tab name and is best avoided
   entirely.
4. **Deploying a second report? It needs its own slug AND its own deploy target/domain.**
   Confirm you are not overwriting an existing deployment or an existing tab.
5. **Before deploying, state the chosen slug to the user** and confirm it is not already in
   use by another live document. Check with `GET <endpoint>?action=projects` (lists all
   existing tabs) or `GET <endpoint>?action=rows&project=<slug>` — a non-empty result means
   the tab is taken.

## Setup: `~/.claude/html-comments.config.json`

This skill needs two URLs of your own, kept in `~/.claude/html-comments.config.json` —
deliberately **outside** the skill folder, so copying or publishing the skill can never
carry your endpoint along (the URL is unauthenticated write access to your sheet). Copy
`config.example.json` there and fill in:

```json
{
  "endpoint": "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec",
  "assetBase": "https://your-asset-host.example/",
  "publishTarget": "your-asset-host.example"
}
```

- **`endpoint`** — your own deployment of the Apps Script collect endpoint. Source and a
  fully scripted deploy recipe (clasp; one manual OAuth-consent click) are in
  `apps_script/Code.gs` + `apps_script/DEPLOY.md`. It is public-by-URL and writes into your
  Google Sheet, so use your own, never someone else's.
- **`assetBase` / `publishTarget`** — a static host you control where `bin/publish.sh` puts
  `html-comments.js` + `.css` (surge.sh works well; so does any host serving
  `cache-control: max-age=0, must-revalidate`). `assetBase` is read only by you/the model
  when writing embed snippets; `publishTarget` is read by `bin/publish.sh`; `endpoint` by
  `bin/resolve.py`.

**Read the config and substitute the values wherever this file writes `<ENDPOINT>` or
`<ASSET-BASE>`.** If the file is missing, stop and ask the user to create it rather than
guessing a URL.

## One canonical copy — never copy the assets into a project

`assets/` here is the source of truth, published to `<ASSET-BASE>` by `bin/publish.sh`.
Every page loads the overlay from that URL, so a fix ships to all reports at once — the
next page load picks it up, no report needs re-rendering or re-deploying.

Do **not** copy `html-comments.js` / `.css` next to a report. That is what caused three
different vintages of the overlay to be live at the same time, one of them frozen inside a
rendered HTML file by `embed-resources: true`.

Workflow for a change: edit `assets/`, bump `HC_VERSION`, run `bin/publish.sh`. The script
refuses to publish if `HC_VERSION` was not bumped, and fails loudly if the live version
does not match after publishing. Check what a page is running with `window.__hcVersion` in
its console. (Loading the script twice on one page is harmless — the second copy refuses
to start.)

## Enable on a page

Add before `</body>`, **replacing `UNIQUE-PROJECT-SLUG` with a fresh slug** (see the warning
above — the overlay refuses to run on the placeholder):

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

### What reviewers see

The overlay is always on: a floating button bottom-right (badge = open-thread count) opens
the sidebar; reviewers select text to get 💬 Comment / ✏️ Suggest. On viewports ≥900px the
open panel **pushes the page content aside** by default so it never covers the text; the ⇥
button in the panel header toggles overlay mode instead (remembered per browser in
localStorage, key `hc-push`). Below 900px the panel always overlays. In the suggestion
composer the original text is pre-filled *and pre-selected*, so typing replaces it the way
it would in Word, while a click or arrow key edits it in place. An identity row at the top
of the sidebar shows who you are posting as and turns amber ("Name required —") until a
name is set; replies require a name, as comments already did. A reply gets a **Submit**
button next to its box as soon as there is text, and the thread shows a blue pending stripe
until it is sent — Resolve and Delete sit on a separate row below. Unsubmitted reply text is
also kept in localStorage per thread. Names are remembered in localStorage; no auth — the
endpoint is public-by-URL, so don't use it for sensitive content, and note that anyone can
type anyone's name (Delete is gated only on the typed name matching).

## Working offline

Reviewing on a plane works, with one precondition: **the page must be opened while online**,
because the overlay itself is fetched from `<ASSET-BASE>` and served with
`max-age=0, must-revalidate`, so a reload with no network gets no overlay at all. Leave the
tab open (a discarded background tab reloads from the network on return).

Given that, the overlay keeps two localStorage stores per project — `hc-cache-<project>`
(the last successful server read) and `hc-outbox-<project>` (records not yet accepted) — and
always renders **cache + outbox**. So offline, a reviewer sees the existing comments and
everything they add, across reloads, with a "N pending" chip in the panel header and a
banner explaining the state. The queue is retried (posts spaced ~1s apart) on page load, on
the `online` event, on return to the tab (`visibilitychange`, only when something is
queued), and when the chip or ↻ is clicked. Authoring time is preserved in the note's
`cts` field, since the server stamps rows at the moment they arrive, not when they were
written.

Records carry unique `itemId`s and the overlay dedups on them. A post the server accepted
but whose response was lost (the endpoint's 302→404 quirk, see below) is detected on the
next successful read — any outbox entry whose `itemId` already appears in the server rows
is dropped rather than re-posted.

## Record format (sheet rows)

Standard endpoint columns; `vote` holds the record type (`comment`, `suggestion`, `reply`,
`resolve`, `reopen`, `delete`), `itemId` the record id, and `note` a JSON payload:
`{v, cts, text, kind, replacement?, anchor:{…}, parentId?}`. Append-only event log; the
**last** resolve/reopen row in server row order decides a thread's state; `delete` hides
its target.

The anchor (`v: 2`, written since overlay 2026-08-11.2) carries enough context to place
even a one-word suggestion:

| field | meaning |
|---|---|
| `quote` | the selected document text — **raw** rendered text, may contain `\n` |
| `prefix` / `suffix` | up to 120 chars of rendered text either side — raw, like `quote` |
| `containerId` | id of the nearest ancestor with an id (`''` if none — then `nth`/`total` are scoped to the whole body) |
| `nth` / `total` | which occurrence of `quote` this is within that container (0-based), of how many |
| `block` | quote-centred window (≤700 chars) of the surrounding paragraph/list-item/cell text, **whitespace-collapsed**, `…` marks truncated ends |
| `blockTag` | tag of the block element (`p`, `li`, `td`, `h2`, …) — `td`/`th`/`caption` warn you the text may be generated |
| `headings` | text of the heading chain in scope (nearest preceding h1, then h2, …), e.g. `["Results", "Robustness checks"]` — maps directly onto `#`/`##` lines in a .qmd |
| `docPos` | fractional position of the quote in the whole document text (0–1) |

Mind the normalization asymmetry: `quote`/`prefix`/`suffix` are raw index text (newlines
preserved), `block` is whitespace-collapsed. **Normalize whitespace on all of them before
matching one inside another.**

Every one of these fields is taken from the document's own text. Text the overlay itself
puts on the page — a shown suggestion's replacement, in a `.hc-ins` span — is never part
of them, even when the reviewer's selection visibly covered it. A selection that spans a
suggestion anchors to the original text being replaced; a selection lying entirely inside
a replacement anchors to the whole passage that suggestion replaces.

Older records: v1 anchors (before 2026-08-11.2) lack `headings`/`blockTag`/`docPos`, their
`block` is head-truncated (the quote may be absent from it), and a comment made while a
suggestion was displayed may have the replacement text spliced into its `quote` — such an
anchor matches nothing in page or source; place it by `prefix`/`suffix` instead and treat
that span, not `quote`, as the text the reviewer meant. Records before 2026-07-25 have only
`quote`, a 30-char `prefix`/`suffix`, and `containerId`.

## Reading comments programmatically

```
GET <endpoint>?action=rows&project=<slug>
→ {"ok":true,"rows":[{ts,itemId,vote,note,voter,session}, ...]}   (oldest first)
GET <endpoint>?action=projects
→ {"ok":true,"projects":["slug-a","slug-b",...]}                  (existing tabs)
```

## Applying comments/suggestions back to the source (.qmd etc.)

When asked to apply collected feedback:

1. Fetch the rows (curl the GET above; `-L` — Apps Script 302-redirects).
   If you POST from curl at all, use `curl -sL --data-binary '<json>' -H 'Content-Type:
   text/plain;charset=utf-8'` WITHOUT `-X POST` — forcing the method re-POSTs to the redirect
   target and returns a Drive "Page not found". For resolve records use `bin/resolve.py`
   instead; see "Resolving threads programmatically" below for why hand-rolling fails.
2. Reduce the log: group by thread root (`parentId`), drop `delete`d records and threads
   whose last resolve/reopen row is `resolve` (unless asked to include resolved).
3. For each open item, locate the passage in the **source** file. Work from the anchor,
   most-oriented first:
   - `headings` tells you which section — find the matching `#`/`##` lines first and search
     within that region.
   - Whitespace-normalize everything before matching (`quote` may contain literal
     newlines; `block` is already collapsed).
   - For short or repeated quotes ("a" → "the"), do not match on `quote` alone: find
     `block` (quote-centred, near-unique; strip any leading/trailing `…`) in the source,
     then use `prefix`/`suffix` to pick the spot, and `nth`/`total` (0-based, scoped to
     `containerId`, or to the whole body when it is `''`) as the final check. If the
     context does not pin down a single site, flag it rather than guessing.
   - The anchor text is *rendered* output: strip markdown/inline-code differences and
     search tolerantly. `blockTag` of `td`/`th`/`caption`, or quotes inside inline-R
     output and citations, will not match the source verbatim — flag these for manual
     handling instead of guessing. A v1 `quote` that is nowhere in the source may carry
     spliced replacement text (see above): fall back to the span between `prefix` and
     `suffix`.
   - Plain-prose matches: apply `suggestion` replacements directly; surface `comment`s to
     the user with file:line locations.
4. After applying, resolve each handled thread — with a short reply saying what was done —
   via ONE `bin/resolve.py --reply-file` call (see below), and note the resolution to the user.

## Replying to and resolving threads programmatically

**Use `bin/resolve.py` for ALL programmatic writes — replies included. Do not hand-roll any
of it with curl or urllib.** Three silent failure modes make a hand-written record look like
it worked when it did not (traps below), and the endpoint itself misbehaves in two ways the
script absorbs:

- **A successful POST often *returns an HTTP error*.** The endpoint appends the row, then
  302-redirects to a one-time Google URL that frequently 404s. Any client that treats that
  404 as failure (curl -f, bare urllib, requests.raise_for_status) will abort a batch whose
  rows are all landing. Success is only ever established by re-reading `?action=rows`.
- **Bursts get rate-limited** — after a rapid run of requests, reads and writes both start
  404ing for a while. The script spaces posts and retries reads with backoff; a hand-rolled
  loop typically dies mid-batch and leaves the job half done.

```
python3 bin/resolve.py <project> --open                     # list open thread ids
python3 bin/resolve.py <project> --all                      # resolve every open thread, verify
python3 bin/resolve.py <project> id1 id2                    # resolve specific threads
python3 bin/resolve.py <project> --all --reply-file a.json  # reply + resolve + verify, one call
python3 bin/resolve.py <project> --all --reopen             # undo: reopen every resolved thread
```

`--reply-file` takes `{"<thread itemId>": "reply text", ...}` and posts each reply
immediately before its thread's resolve, so answering a whole review round is a single
command: write the JSON, run once, read the verification line. With `--reply-file`, only
threads present in the file are targeted. The script mints globally unique (timestamped)
`itemId`s itself; it validates explicit thread ids up front (a typo'd id aborts before
anything is posted), treats already-done targets as success (reruns exit 0), and re-reads
the log at the end, verifying that targeted threads reached the intended state **and** that
posted replies actually landed — that exit status, not the per-post responses, is the
result. `--reopen` targets *resolved* threads and verifies they came back open.

### The three traps

1. **`{"ok":true}` is not evidence that anything useful was written.** The original shared
   endpoint accepted form-encoded POSTs, swallowed the JSON parse failure, and appended a
   blank row to a junk `default` tab. The endpoint in `apps_script/Code.gs` rejects
   non-JSON bodies and missing `project`s outright — but either way, only re-reading
   `?action=rows` and comparing row counts proves a write. The endpoint only parses a JSON
   body sent as `Content-Type: text/plain;charset=utf-8`.

2. **Reusing the thread's `itemId` makes the record vanish.** `composeRows()` in
   `assets/html-comments.js` dedupes on `itemId` and keeps the **first** occurrence. Server
   rows arrive oldest-first, so a resolve posted with `itemId` equal to the comment's own id
   loses to the comment row and is dropped before it is ever interpreted. The row sits in the
   sheet and the thread stays open in the sidebar forever. **Every record needs a fresh,
   unique `itemId`** — that is what the overlay itself does (`submitStatus()` calls `genId()`).

3. **`note.parentId` is mandatory.** `buildThreads()` reads resolve/reopen targets from
   `note.parentId` *only*; there is no fallback to the record's own `itemId`. A resolve whose
   note omits `parentId` is written and then ignored. The note must be a JSON **string**:
   `{"v":2,"parentId":"<thread itemId>"}`.

So a correct record is:

```json
{"project":"…","itemId":"<fresh unique id>","vote":"resolve",
 "note":"{\"v\":2,\"parentId\":\"<thread itemId>\"}","voter":"…","session":"…"}
```

### Verify with the overlay's own rules, not a lenient reduction

This is what has repeatedly turned a broken resolve into a false "all closed" report. Ad-hoc
reduction scripts (including the `show_comments.py`-style helper some projects keep) commonly
write `note.get('parentId') or itemId` and dedupe leniently. That accepts records the overlay
rejects, so the check passes while the reviewer's sidebar still shows every thread open.
`bin/resolve.py --open` reduces exactly as `html-comments.js` does; trust it over any local
helper, and if you must write your own, mirror both rules from trap 2 and trap 3.

Note also that a reviewer with the page already open sees the open-thread badge drop only
after their overlay next refreshes (reload, ↻, or returning to the tab with a queued post).

## Debug hooks

`window.__hcVersion` exposes the running version and `window.__hcState()` internal state.
`window.__hcInjectRows(rows)` renders fake rows through the real pipeline — but a later
server refresh replaces them; to keep injected rows put, pass them as `debugRows` in
`HC_CONFIG` instead, which also skips the server read. A missing/old backend or a
misconfigured page shows a banner instead of failing silently (offline, server-error, and
config errors have distinct messages). See "Working offline" above for how failed POSTs
are queued and retried.
