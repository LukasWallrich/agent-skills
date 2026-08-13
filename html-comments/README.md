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
