---
name: html-comments
description: Add a Google-Docs-style comment + suggested-edit layer to any static HTML page (Quarto reports, plain HTML), whether opened locally as a file or published anywhere (surge.sh, GitHub Pages), and maintain the shared overlay assets. Use when the user wants reviewers to be able to comment on / suggest edits to an HTML page, or to annotate an HTML plan themselves. To publish a page with the layer already on, see deploy-html; to read the collected comments back or apply them to the source, see review-comments.
---

# HTML Comments — reviewer comments + suggested edits on static HTML

A self-contained overlay (`assets/html-comments.js` + `assets/html-comments.css`, no
dependencies) that lets readers select text, leave threaded comments or suggested edits,
resolve/reopen threads, and see everyone else's comments. Storage is a Google Apps Script
endpoint of your own (source + deploy recipe in `apps_script/`), configured in
`~/.claude/html-comments.config.json`.

## ⚠️ FIRST: choose a unique `data-project` slug

The whole system keys on one string: the project slug. It names the Google Sheet **tab**
every comment on the page reads from and writes to. **The single most important step in
this skill is giving each distinct document its own slug.**

- **Reusing a slug across two different documents** (or forgetting to change the
  `UNIQUE-PROJECT-SLUG` placeholder) points both pages at the *same* tab. Each page then
  tries to anchor the other page's comments into text that doesn't exist there, so comments
  render as orphans, land on the wrong passage, or vanish. This is not recoverable by editing; it corrupts the shared
  log.

## Check the slug before embedding it — always

```sh
~/.claude/skills/html-comments/bin/check-slug.sh <slug>
```

Exit 0 means free, exit 3 means another document already owns it, exit 1 means the check
could not be made — treat that as unsafe too, and do not embed the slug.

Run it whenever you put a slug into a page by hand: a Quarto `comments-include.html`, a
GitHub Pages report, any page not published through `deploy-html`. That skill runs this same
script itself, so a deployed page is already covered.

**Reserve the slug in the same step**, so a second document cannot take the name in the
window before this page receives its first comment:

```sh
~/.claude/skills/html-comments/bin/check-slug.sh <slug> --claim <page-url>
```

Flags:

- `--claim <url>` — records the slug in the `_slugs` registry against that URL. Re-running
  for the same URL is fine; the registry, not the response, decides who holds it.
- `--allow-existing-at <url>` — a slug already held is fine when the page at that URL *is*
  the document holding it. This is how a redeploy of a revised version passes.
- `--force` — skips the check. Only when you know the slug is that same document.

A slug counts as taken if the registry holds it **or** comments already exist under it. The
registry is what covers a page that is live but not yet commented on — a tab appears only on
the first write, so the tab list alone would call that slug free.

Rules:
1. **One slug ↔ one document.** Never reuse a slug for different content. Derive it from the
   document (e.g. `zcurve-predictive-accuracy`), not from a generic word like `report` or
   `draft`.
2. **New version you want reviewed separately → new slug** (e.g. append `-v2`). Comments do
   not migrate between slugs.
3. **Stick to `[a-z0-9-]` and ≤90 chars.** The endpoint sanitises `\ / ? * [ ] :` out of
   tab names on write; this can lead to clashes, so avoid.
4. **Deploying a second report? It needs its own slug AND its own deploy target/domain.**
   Confirm you are not overwriting an existing deployment or an existing tab.
5. **State the chosen slug to the user** before putting it in a page, and run
   `bin/check-slug.sh` on it.

## Config

Two URLs live in `~/.claude/html-comments.config.json`: `endpoint` (the Apps Script collect
endpoint) and `assetBase` (the host serving the overlay files). **Read them from that file
and substitute them wherever this document writes `<ENDPOINT>` or `<ASSET-BASE>`.**

If the file is missing, stop and ask the user to create it — never guess a URL, and never
move the file into the skill folder (the endpoint is unauthenticated write access to their
sheet). `README.md` covers first-time setup.

## One canonical copy — never copy the assets into a project

`assets/` here is the source of truth, published to `<ASSET-BASE>` by `bin/publish.sh`.
Every page loads the overlay from that URL, so a fix ships to all reports at once.

Workflow for a change: edit `assets/`, bump `HC_VERSION`, run `bin/publish.sh`. Check what a page is running with `window.__hcVersion` in
its console.

## Enable on a page

The deploy-html skill does this for you when it deploys. Do it by hand for Quarto (see below),
for a page opened locally from disk, or when the page is published somewhere else.

Add before `</body>`, **replacing `UNIQUE-PROJECT-SLUG` with a slug you have just run
`bin/check-slug.sh` on**:

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

Keep the snippet in this form. A plain `<script src>` tag gets inlined by pandoc's
`embed-resources: true`, freezing a copy of the overlay into that rendered HTML so the page
never picks up later fixes; a URL inside a JS string is invisible to it.

For **Quarto**: put the snippet in a `comments-include.html` next to the .qmd and add
`include-after-body: comments-include.html` to the HTML format. Re-rendering keeps the
layer. Note that a `comments-include.html` copied from another project will carry that
project's slug — change it.

### What reviewers see

A floating button bottom-right (badge = open-thread count) opens the sidebar; selecting text
offers 💬 Comment or ✏️ Suggest. Threads can be replied to, resolved and reopened. Posting
requires a name, remembered in that browser.

There is no auth and the endpoint is public-by-URL: keep sensitive content off any page
carrying the layer, and treat names as claims, not identities.

## Working offline

Reviewers can work offline: the overlay caches the log and queues new records in
localStorage, retrying when the network returns. One precondition — **the page must have
been opened while online**, because the overlay itself loads from `<ASSET-BASE>`, so tell
reviewers to leave the tab open rather than reloading on a plane. `README.md` has the
mechanics.

## Reading the comments back

Everything after the reviewing — reading the log, reporting what reviewers said, locating
each item in the source, applying suggestions, replying to and resolving threads — is the
**review-comments** skill.

## Debug hooks

`window.__hcVersion` exposes the running version and `window.__hcState()` internal state.
`window.__hcInjectRows(rows)` renders fake rows through the real pipeline — but a later
server refresh replaces them; to keep injected rows put, pass them as `debugRows` in
`HC_CONFIG` instead, which also skips the server read. A missing/old backend or a
misconfigured page shows a banner instead of failing silently (offline, server-error, and
config errors have distinct messages). See "Working offline" above for how failed POSTs
are queued and retried.
