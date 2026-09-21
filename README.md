# agent-skills

Claude Code skills I use for research work. Vibe-coded, and they work for me — read the
code, expect sharp edges, and use at your own risk.

Companion repo: [revealjs-tools](https://github.com/LukasWallrich/revealjs-tools) (skills for
Reveal.js decks).

## Install

Each directory is one skill. Clone the repo somewhere and symlink the skills you want into
`~/.claude/skills/`:

```sh
git clone https://github.com/LukasWallrich/agent-skills.git ~/Coding/agent-skills
ln -s ~/Coding/agent-skills/html-comments  ~/.claude/skills/html-comments
ln -s ~/Coding/agent-skills/download-paper ~/.claude/skills/download-paper
ln -s ~/Coding/agent-skills/upload-public  ~/.claude/skills/upload-public
```

Symlinking rather than copying means one source of truth — a fix reaches every project at
once instead of leaving stale vintages behind.

## Skills

### `html-comments`

A Google-Docs-style comment and suggested-edit layer for any static HTML page — Quarto
reports, plain HTML, anything you can deploy. Reviewers select text and leave threaded
comments or suggested edits; threads resolve, reopen, and round-trip between reviewers.
Comments live in a Google Sheet behind an Apps Script endpoint, so the page itself stays
static and can be hosted anywhere.

The overlay is dependency-free vanilla JS, loaded at runtime from one host you control, so
every document runs the same version. Suggestions store enough surrounding context
(paragraph text, occurrence index, 120 chars either side) that an agent can place even a
one-word change back into the source `.qmd`.

**Setup:** copy `config.example.json` to `config.local.json` and fill in your own Apps
Script endpoint and asset host. Both are yours to deploy — the endpoint is public-by-URL
and writes into your Google Sheet, so don't point it at someone else's.

### `download-paper`

Downloads academic PDFs by DOI for claim verification: local cache, then the
[`fetchpdf`](https://github.com/The-Metascience-Observatory/fetchpdf) open-access chain
(OpenAlex, Unpaywall, PubMed Central, Crossref links, preprint servers, repositories,
publisher routes), then Google Scholar via SerpAPI, then your own running, signed-in Chrome
for whatever is paywalled. Every downloaded PDF is checked against the requested DOI, title
and page range, so a topically-similar paper or a first-page preview is rejected and the
search continues rather than being cached under the wrong DOI.

The Chrome step needs no manual step: on macOS, with Chrome running and *View ▸ Developer ▸
Allow JavaScript from Apple Events* switched on, it opens one unfocused tab, walks the
publisher's PDF URLs and EBSCOhost through your existing logins, and closes the tab again.
Headless browsers are Cloudflare-flagged, so it drives the real Chrome via `osascript`. Set
`INSTITUTION_EBSCO_PROFILE` to your library's EBSCO cluster id to include EBSCO.
`--no-browser` leaves the run entirely non-interactive.

`download_paper.py` is a `uv run --script` file: run it directly, and uv installs its
dependencies on first use. `institutional_fetch.py` holds the Chrome layer, and its CLI
runs a single route (EBSCO, or a cookie-jar fetch of the publisher's PDF URL) against one
DOI.

`SERPAPI_API_KEYS` (comma-separated, tried in order; enables the Google Scholar step) and
`RESEARCHER_EMAIL` (contact address for the open-access APIs) are read from the
environment, then from `~/.claude/api_keys.env`.

### `upload-public`

Uploads a screenshot, image, video or any other file to your own Cloudflare R2 bucket and
prints a public URL plus a ready-to-paste markdown snippet — for pasting a screenshot into
a GitHub issue or PR, or anywhere else that cannot read a local path. Takes the macOS
clipboard image directly with `--clipboard`. Object keys carry a content hash, so the same
file keeps the same URL.

`--gif` converts a screen recording to an animated GIF before uploading, because GitHub's
`media-src` policy lists only GitHub's own hosts: an externally hosted `.mp4` renders as a
player that never loads, while a GIF goes through GitHub's camo image proxy and animates.

**Setup:** needs the `aws` CLI, and `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_ENDPOINT`, `R2_PUBLIC_BUCKET` and `R2_PUBLIC_BASE` in `~/.claude/api_keys.env`. Create
the bucket with `wrangler r2 bucket create <name>` and `wrangler r2 bucket dev-url enable
<name>`, which prints the public base. R2's free tier is 10 GB with no egress charge. The
bucket is public: anything uploaded is readable by anyone with the URL.

## Licence

MIT.
