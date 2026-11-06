# Find PDF API for Zotero

Lets a tool on your own computer ask Zotero to run **Find Available PDF** for one item. The
`download-paper` skill uses it for `--zotero-find-pdf`. Without the plugin, that flag only logs a
line and the download carries on as normal.

## Not allowed with resolvers that break copyright

The plugin runs every lookup Zotero has set up, **including your custom PDF resolvers**, and it does
not filter them. Do not use it if your Zotero has a custom resolver that breaks copyright, such as
Sci-Hub, Anna's Archive, LibGen or Z-Library. Resolvers for access your institution has paid for
are fine. To see yours, open Settings ▸ Advanced ▸ Config Editor and search for
`extensions.zotero.findPDFs.resolvers`.

## Install

You need Zotero 10.

1. Download [`find-pdf-api-0.1.1.xpi`](https://raw.githubusercontent.com/LukasWallrich/agent-skills/main/download-paper/zotero-plugin/dist/find-pdf-api-0.1.1.xpi),
   or use that file in this checkout's `dist/` directory.
2. In Zotero, open Tools ▸ Plugins, click the gear icon, choose *Install Plugin From File…* and
   select the file.
3. In Settings ▸ Advanced, tick *Allow other applications on this computer to communicate with
   Zotero*.

The first time `download-paper` asks to write to your library, Zotero asks for permission.
Choose *Always Allow*: that tool needs several writes and saves the local API key for later runs.

## What to expect

- It only works while Zotero is open, and it handles one item at a time.
- A lookup can take a few minutes. A site's captcha may open a window in Zotero.
- A new attachment and its parent item are tagged `agent-added`. Items where nothing was found are left
  as they were.
- Zotero can also return an EPUB. The endpoint reports its `contentType`; `download-paper`
  rejects and removes files that are not PDFs.
- `download-paper` checks that a new PDF is the requested paper, and removes it from your library
  if it is not. If a tool is stopped halfway, a PDF can stay in your library unchecked, with the
  `agent-added` tag.
- A new Zotero major version switches the plugin off until a version supporting it is released,
  because the plugin relies on parts of Zotero that can change between versions.

Plugin updates arrive through Zotero's normal update check. To remove the plugin, use Tools ▸ Plugins.

## How it works

`POST http://127.0.0.1:23119/find-pdf-api/v1/find` with the JSON body `{"itemKey": "ABCD2345"}`, in
the personal library only. Set `Content-Type: application/json`. Requests from web pages are
refused, and callers cannot choose lookup methods or resolvers.

Authenticate with Zotero's **local** API key, which is separate from a zotero.org API key:

1. Read the `Zotero-Server-ID` response header from
   `GET http://127.0.0.1:23119/api/users/0/items?limit=1`.
2. If the caller has no saved key for this server, send
   `POST http://127.0.0.1:23119/api/local/authorize` with `Content-Type: application/json`,
   the `Zotero-Server-ID` header, and `{"appName":"Your tool name"}`. Zotero displays a
   permission dialog and returns a JSON `key` after approval. A one-time key is enough for
   one lookup; callers making several writes should request *Always Allow* and store the
   key privately.
3. Send both `Zotero-Server-ID` and `Zotero-API-Key` as headers on the plugin request.
   Keys in query strings are refused. Zotero's own authentication check also accepts a
   local key in an `Authorization: Bearer …` header.

For an existing eligible item, a successful response looks like:

```json
{
  "state": "found",
  "attachmentKey": "EFGH6789",
  "contentType": "application/pdf",
  "itemTagged": true,
  "methodsTried": ["doi"],
  "version": "0.1.1"
}
```

`itemTagged` says whether this request added the parent's `agent-added` tag.
`methodsTried` records the lookup methods started, in order; it is not a list of URLs.
Read the attachment through Zotero's local API, for example
`GET /api/users/0/items/EFGH6789/file/view/url` for its local file URL.
**`found` means Zotero attached a file; it does not verify the paper's identity.** A caller
using this endpoint directly must check the file itself. `download-paper` performs that check.

| HTTP status | `state` | Meaning |
|---|---|---|
| 200 | `found` | A file was attached and tagged |
| 200 | `none` | No file was found |
| 200 | `not-eligible` | The item is in the bin, already has a PDF/EPUB, or lacks a usable identifier or URL |
| 200 | `item-not-found` | No item with that key exists in the personal library |
| 200 | `timeout` | The search exceeded four minutes |
| 409 | `busy` | Another request is still running or cleaning up |
| 403 | `local-api-off`, `forbidden` | Local API access is disabled, or the request has an Origin header |
| 400 | `bad-request` | The body must contain only one eight-character `itemKey` |
| 501 | `unsupported` | This Zotero lacks the internal functions the plugin needs |
| 500 | `error` | The lookup, tagging, or cleanup failed; a short `message` is included |

Zotero's server and authentication errors can be plain text, without a `state`: for example,
401 for a missing/expired key, 412 for the wrong server ID, 428 for a missing server ID,
or 404 when the plugin is absent. Invalid JSON, content types and methods are also handled
by Zotero before the plugin runs. Callers must check the HTTP status and content type.
Zotero can also close connections from web pages without an HTTP response before the
plugin sees them.

Allow a client timeout longer than four minutes (`download-paper` allows five). After a
plugin timeout, the underlying Zotero search can continue; the plugin keeps refusing new
lookups until it finishes and deletes a file that arrives late. Cleanup failures are logged
in Zotero's error console and may leave an attachment requiring manual removal.

## For maintainers

From this directory, change `src/`, raise `version` in `src/manifest.json`, then run
`python3 build.py` and commit
`dist/` and `update.json`. The build refuses to change a version it has already built, because
Zotero only offers an update when the version goes up. Tests: `node --test test/handler.test.js`.
Run `python3 build.py --check` to verify the release package and update hash, and update the
download link above when the version changes. Keep older release packages for existing links.

The endpoint delegates to Zotero's internal attachment and local API authentication functions.
Unit tests use mocks; check those interfaces against the target Zotero version and smoke-test
an installed release before publishing it. Version 0.1.0 was tested in Zotero 10.0.7: an
authenticated request attached the nine-page *A manifesto for reproducible science*
(DOI `10.1038/s41562-016-0021`) to an existing item without a PDF in 1.4 seconds, and the
downloaded file's title and DOI matched.
Version 0.1.1 was also loaded in Zotero 10.0.7 and checked for authenticated
`not-eligible`/`item-not-found` replies, missing credentials, and Origin rejection.
