# Installing html-comments on a machine

One-time setup. Day-to-day use is in `SKILL.md`.

## 1. Configure `~/.claude/html-comments.config.json`

Copy `config.example.json` there and fill in your own values:

```json
{
  "endpoint": "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec",
  "assetBase": "https://your-asset-host.example/",
  "publishTarget": "your-asset-host.example"
}
```

It lives **outside the skill folder** on purpose: the endpoint URL is unauthenticated write
access to your sheet, so copying, syncing or publishing the skill must not be able to carry
it along. Do not move it inside.

| key | read by |
|---|---|
| `endpoint` | `bin/check-slug.sh`, `review-comments/bin/resolve.py`, and the snippet embedded in each page |
| `assetBase` | you and the model, when writing an embed snippet |
| `publishTarget` | `bin/publish.sh` |
| `adminToken` | `bin/check-slug.sh --release` only. Optional — omit it and releasing is simply unavailable. It must match `ADMIN_TOKEN` in the deployed `Code.gs`, and must never appear in a published page or a public repo. |

## 2. Deploy your own endpoint

`apps_script/Code.gs` plus the recipe in `apps_script/DEPLOY.md` (clasp; one manual
OAuth-consent click). It is public-by-URL and writes into your Google Sheet, so use your
own — never someone else's.

## 3. Publish the overlay assets

Pick a static host you control that serves `cache-control: max-age=0, must-revalidate`
(surge.sh does), put it in `publishTarget`/`assetBase`, then run `bin/publish.sh`. Every
page loads `html-comments.js` + `.css` from there, so later fixes reach all reports at once.

## 4. For deploying documents

The `deploy-html` skill also needs the `surge` CLI on PATH and an authenticated surge
account (`surge login`, or `SURGE_LOGIN`/`SURGE_TOKEN`, or a `~/.netrc` entry for
`machine surge.surge.sh`).

## How Edit mode turns typing into suggestions

The ✏️ button sets `contenteditable` on leaf text blocks (`p`, `li`, `h1`–`h6`, `td`, `th`,
`dd`, `dt`, `figcaption`, `caption`, `blockquote`; a block containing another block is
skipped in favour of the inner one). Nothing is saved while the reviewer types. When a block
loses focus:

1. Its edited text is read from the DOM: the original text, minus the reviewer's own
   Edit-mode deletions, plus their own insertions; other reviewers' proposed insertions are
   excluded.
2. That text is diffed against the block's original text. Tokens are words and whitespace
   runs, any two whitespace runs count as equal (source line breaks never become changes),
   and the longest common subsequence of tokens gives the changes. A run of changed tokens
   with no unchanged word inside it is one change, so an insertion typed directly next to a
   replaced word joins it. Changes separated only by whitespace also merge into one, unless
   one of them is already a suggestion of its own.
3. Each change is posted as a normal `suggestion` record whose anchor is built from the
   restored block by the same code as a mouse selection. A pure insertion quotes its
   neighbouring word and repeats it in the replacement.
4. The block's DOM is replaced by a clean copy taken when it gained focus, and the page
   re-renders the suggestions as tracked changes. A refresh that arrives mid-edit waits
   until the block is committed.

The supersede rule compares each of the reviewer's existing Edit-mode suggestions in the
block with the new changes: identical leaves it alone, same words with a new replacement
posts an `edit`, and anything else retires it (`delete`, or `resolve` plus a reply when the
thread has replies). Focus and blur without typing posts nothing.

## How offline review works

The overlay keeps two localStorage stores per project — `hc-cache-<project>` (the last
successful server read) and `hc-outbox-<project>` (records not yet accepted) — and always
renders cache + outbox. Offline, a reviewer therefore sees the existing comments plus
everything they add, across reloads, with an "N pending" chip in the panel header and a
banner explaining the state.

The queue is retried with posts spaced about a second apart: on page load, on the `online`
event, on return to the tab (`visibilitychange`, only when something is queued), and when
the chip or the refresh control is clicked.

The precondition is that the page was opened while online — the overlay is fetched from
`assetBase` with `max-age=0, must-revalidate`, so a reload with no network gets no overlay
at all. A discarded background tab counts as a reload.

Records carry unique `itemId`s and the overlay dedupes on them, so a post the server
accepted but whose response was lost (the endpoint 302-redirects to a URL that often 404s)
is not sent twice: any outbox entry whose `itemId` already appears in the server rows is
dropped on the next successful read.
