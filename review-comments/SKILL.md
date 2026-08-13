---
name: review-comments
description: Read back the comments and suggested edits reviewers left on an HTML page through the html-comments overlay. Use if you need to summarise them, apply the suggestion or resolve threads. Use when the user asks what comments are, wants collected feedback summarised or applied, or wants threads resolved or reopened.
---

# Review comments — reading them back and acting on them

Comments made through the html-comments overlay live in a Google Sheet, one tab per project
slug, reachable through an Apps Script endpoint.

Putting the layer on a page is the **html-comments** skill; deploying a page with it already
on is **deploy-html**.

## What you need first

The **endpoint** comes from `~/.claude/html-comments.config.json` (`endpoint` key). If it is
missing, stop and ask — do not guess a URL.

The **slug** identifies the document's tab. Usually it is visible in the
page's snippet (`project: '<slug>'`) or its `<!-- hc-doc: <slug> -->` marker. To look one up:

```
GET <endpoint>?action=projects
→ {"ok":true,"projects":["slug-a","slug-b",...]}                  (existing tabs)
```

## Reading the log

```
GET <endpoint>?action=rows&project=<slug>
→ {"ok":true,"rows":[{ts,itemId,vote,note,voter,session}, ...]}   (oldest first)
```

Use `curl -L` — Apps Script 302-redirects. Bursts get rate-limited (reads start 404ing for a
while), so retry with backoff rather than hammering.

Reduce before reporting: group by thread root (`parentId`), drop `delete`d records and
threads whose last resolve/reopen row is `resolve`, unless asked to include resolved ones.

## Record format (sheet rows)

Standard endpoint columns; `vote` holds the record type (`comment`, `suggestion`, `reply`,
`resolve`, `reopen`, `delete`), `itemId` the record id, and `note` a JSON payload:
`{v, cts, text, kind, replacement?, anchor:{…}, parentId?}`. Append-only event log; the
**last** resolve/reopen row in server row order decides a thread's state; `delete` hides
its target. `cts` is when the reviewer wrote the record, the row's `ts` when the server
received it — they differ for anything written offline and queued, so order by `cts` when
reporting what a reviewer said.

The anchor carries enough context to place even a one-word suggestion:

| field | meaning |
|---|---|
| `quote` | the selected document text — **raw** rendered text, may contain `\n` |
| `prefix` / `suffix` | up to 120 chars of rendered text either side — raw, like `quote`. Sliced from the document text stream, so they **cross block boundaries**: the prefix of a paragraph-initial quote comes from the end of the previous paragraph and will not match contiguously in the source. |
| `containerId` | id of the nearest ancestor with an id (`''` if none — then `nth`/`total` are scoped to the whole body) |
| `nth` / `total` | which occurrence of `quote` this is within that container (0-based), of how many |
| `block` | quote-centred window (≤700 chars) of the surrounding paragraph/list-item/cell text, **whitespace-collapsed**, `…` marks truncated ends. Confined to **one** block element, so it maps onto a single source paragraph — this is the field to match on. |
| `blockTag` | tag of the block element (`p`, `li`, `td`, `h2`, …). Note that content might be auto-generated in the source file, especially tables, and thus appear nowhere in the source — try your best to match to source; ask if needed. |
| `headings` | text of the heading chain in scope (nearest preceding h1, then h2, …), e.g. `["Results", "Robustness checks"]` — maps directly onto `#`/`##` lines in a .qmd |
| `docPos` | fractional position of the quote in the whole document text (0–1) |

Line wrapping varies between the rendered page, these fields and the source, so **collapse
whitespace on both sides before matching.**

`prefix`/`suffix` and `block` overlap but are not interchangeable. The overlay itself reads
`prefix`/`suffix` to re-place a comment in the rendered page; it never reads `block`, which
exists only for finding the passage in the source.

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

## Applying comments/suggestions back to the source (.qmd etc.)

For each open item, locate the passage in the **source** file. Work from the anchor,
most-oriented first:

- `headings` tells you which section — find the matching `#`/`##` lines first and search
  within that region.
- Whitespace-normalize everything before matching (`quote` may contain literal newlines;
  `block` is already collapsed).
- For short or repeated quotes ("a" → "the"), do not match on `quote` alone: find `block`
  (quote-centred, near-unique; strip any leading/trailing `…`) in the source, then use
  `prefix`/`suffix` to pick the spot, and `nth`/`total` (0-based, scoped to `containerId`,
  or to the whole body when it is `''`) as the final check. If the context does not pin down
  a single site, flag it rather than guessing.
- The anchor text is *rendered* output: strip markdown/inline-code differences and search
  tolerantly.
- Plain-prose matches: apply `suggestion` replacements directly where uncontroversial; where you want user input on comments or suggestions, surface them to the user with file:line locations.

After applying, resolve each handled thread — with a short reply saying what was done — via
ONE `bin/resolve.py --reply-file` call, and note the resolution to the user.

## Replying to and resolving threads programmatically

**Use `bin/resolve.py` for ALL programmatic writes — replies included. Do not hand-roll any
of it with curl or urllib.** Two endpoint quirks it absorbs, so that its exit status is the
result rather than any individual response:

- **A successful POST often *returns an HTTP error*.** The endpoint appends the row, then
  302-redirects to a one-time Google URL that frequently 404s. Any client that treats that
  404 as failure (curl -f, bare urllib, requests.raise_for_status) will abort a batch whose
  rows are all landing. Success is only ever established by re-reading `?action=rows`.
- **Bursts get rate-limited** — after a rapid run of requests, reads and writes both start
  404ing for a while. The script sends every record of a run in **one** batch request and
  retries reads with backoff; a hand-rolled loop posting record by record typically dies
  mid-batch and leaves the job half done.

```
python3 ~/.claude/skills/review-comments/bin/resolve.py <project> --open                     # list open thread ids
python3 ~/.claude/skills/review-comments/bin/resolve.py <project> --all                      # resolve every open thread, verify
python3 ~/.claude/skills/review-comments/bin/resolve.py <project> id1 id2                    # resolve specific threads
python3 ~/.claude/skills/review-comments/bin/resolve.py <project> --all --reply-file a.json  # reply + resolve + verify, one call
python3 ~/.claude/skills/review-comments/bin/resolve.py <project> --all --reopen             # undo: reopen every resolved thread
```

If you POST from curl for anything else, use `curl -sL --data-binary '<json>' -H
'Content-Type: text/plain;charset=utf-8'` WITHOUT `-X POST` — forcing the method re-POSTs to
the redirect target and returns a Drive "Page not found".

`--reply-file` takes `{"<thread itemId>": "reply text", ...}` and posts each reply
immediately before its thread's resolve, so answering a whole review round is a single
command: write the JSON, run once, read the verification line. With `--reply-file`, only
threads present in the file are targeted. The script mints globally unique (timestamped)
`itemId`s itself; it validates explicit thread ids up front (a typo'd id aborts before
anything is posted), treats already-done targets as success (reruns exit 0), and re-reads
the log at the end, verifying that targeted threads reached the intended state **and** that
posted replies actually landed — that exit status, not the per-post responses, is the
result. `--reopen` targets *resolved* threads and verifies they came back open.

### Do not write your own client — for writing or for reading

A record can be accepted and then silently ignored: a reused `itemId` loses to the row it
duplicates, a note without `parentId` is never read as a resolve, and a body sent with the
wrong content type is swallowed. Each failure looks like success. `resolve.py` gets all of
them right and its docstring explains each one if you need to read further.

The same applies to reading. Ad-hoc reduction scripts commonly write
`note.get('parentId') or itemId` and dedupe leniently, which accepts records the overlay
rejects — that is what turns a broken resolve into a false "all closed" report.
`resolve.py --open` reduces exactly as the overlay does; trust it over any local helper.

Note also that a reviewer with the page already open sees the open-thread badge drop only
after their overlay next refreshes (reload, ↻, or returning to the tab with a queued post).
