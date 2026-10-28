---
name: gsheet-collect-endpoint
description: HTTP contract for the FORRT shared Google Apps Script endpoint that appends rows to a Google Sheet (one tab per `project`) and returns per-item vote tallies. Use ONLY when wiring another app to POST votes/records to, or GET a tally from, this specific shared vote/collect endpoint.
---

# FORRT shared Google-Sheets collect endpoint

A single Google Apps Script web app writes rows into one Google Sheet workbook, using a
**separate tab per `project`**. Any app can send data by POSTing to the `/exec` URL with a
different `project` value — no redeploy is needed to add a new campaign/tab. The source lives in
`forrt_shop/vote_app/apps_script/Code.gs` (+ `DEPLOY.md`).

## Endpoint URL

The URL lives in `~/.claude/gsheet-collect.config.json` as `endpoint`. **Read it from that file
and substitute it wherever this document writes `ENDPOINT` or `<ENDPOINT>`.** If the file is
missing, stop and ask the user to create it from `config.example.json` — never guess a URL. The
endpoint writes into FORRT's sheet, so anyone outside FORRT should deploy their own copy of the
script instead.

In an app, store the URL once (e.g. in a `config.js`), not in many places. The `/exec` URL is
stable across code edits as long as you edit the existing deployment.

## Submit a record — `POST`

Send a **`text/plain`** body (this keeps it a "simple" CORS request → no preflight, so a
browser/GitHub-Pages app can POST cross-origin with zero CORS setup). The body is JSON:

```js
fetch(ENDPOINT, {
  method: "POST",
  headers: { "Content-Type": "text/plain;charset=utf-8" },
  body: JSON.stringify({
    project: "my-project-2026", // REQUIRED — names the sheet tab; created on first write
    itemId:  "design-042",      // what is being voted on / the record key
    vote:    "love",            // for tally to work, use one of: keep | discard | love
    note:    "optional comment",
    voter:   "optional voter id/name",
    session: "optional session id"
    // userAgent is captured server-side; you may also send it explicitly
  }),
  keepalive: true
})
```

Response: `{"ok": true}` on success, or `{"ok": false, "error": "..."}`.

Each POST appends one row with columns:
`timestamp, project, itemId, vote, note, voter, session, userAgent`.
Unknown/extra JSON fields are ignored; missing fields become blank. A new `project` value
auto-creates a new tab with this header row.

**Reliability:** POST is best-effort/fire-and-forget. For guaranteed delivery, queue failed posts
locally (e.g. localStorage outbox) and retry — the endpoint is idempotent only in that duplicate
rows are simply appended, so retry logic should avoid double-sending.

## Read data — `GET`

**Health check** — confirm the endpoint is live:
```
<ENDPOINT>?action=ping
→ {"ok":true,"service":"forrt-vote","ts":"..."}
```

**Tally** — per-item vote counts for one project:
```
<ENDPOINT>?action=tally&project=my-project-2026
→ {"ok":true,"project":"my-project-2026",
   "tally":{"design-042":{"keep":3,"discard":1,"love":9}, ...}}
```
Only the three vote values `keep`, `discard`, `love` are counted; other `vote` strings are stored
but not tallied. An unknown project returns an empty `tally`.

**Raw rows** (since deployment @2) — full row log for one project, oldest first:
```
<ENDPOINT>?action=rows&project=my-project-2026
→ {"ok":true,"project":"...","rows":[{"ts":"ISO","itemId":"...","vote":"...","note":"...","voter":"...","session":"..."}]}
```
`userAgent` is not returned. The `html-comments` overlay uses the same `rows` format but its
own deployment (different `Code.gs`, no `tally`); its URL is `endpoint` in
`~/.claude/html-comments.config.json`. Querying this endpoint for comment data returns empty
rows. `?action=ping` tells them apart: `service` is `"forrt-vote"` here, `"html-comments"` there.

## Platform behaviour every client must handle

These come from Apps Script's `/exec` hosting, not from `Code.gs`, so they apply to any
deployment of this contract:

- **A successful POST often returns an HTTP error.** The endpoint appends the row, then
  302-redirects to a one-time Google URL that frequently 404s (with an HTML body). Clients that
  treat a non-2xx status or an unparseable body as failure (`curl -f`, bare `urllib`,
  `raise_for_status`) will mis-report writes that landed. Success is only established by
  re-reading `?action=rows`. When POSTing with curl, send the body with `--data-binary` and do
  NOT force `-X POST` — forcing the method re-POSTs to the redirect target and returns a Drive
  "Page not found".
- **Bursts get rate-limited.** After a rapid run of requests, reads and writes both start 404ing
  for a while. Space posts (~1 s apart) and retry reads with backoff.

## Notes / limits

- `project` is sanitised to a valid tab name (≤90 chars; `\ / ? * [ ] :` → `_`) **on write
  only** — `action=rows`/`tally` look the raw value up. A slug containing those characters
  writes to one tab and reads another (empty) one; keep slugs to `[a-z0-9-]`.
- **A POST with a missing `project` or a non-JSON body is accepted** (`{"ok":true}` for
  form-encoded bodies, whose parse failure is swallowed) **and appends a blank row to a
  `default` tab.** `{"ok":true}` is not evidence a row landed where you meant.
- Writes are serialised with a script lock, so concurrent votes are safe.
- Anyone with the URL can POST (access = "Anyone"); treat it as public, unauthenticated intake.
  Don't send anything sensitive.
- To change server behaviour you must edit `Code.gs` and re-deploy a new version (see
  `apps_script/DEPLOY.md`); adding a new project/tab does **not** require redeploy.
