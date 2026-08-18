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

Downloads academic PDFs by DOI for claim verification, trying, in order: local cache, OSF
preprints, Unpaywall, repository landing-page scraping, Google Scholar via SerpAPI, and
headless-browser fetching. `institutional_fetch.py` handles paywalled papers your library
subscribes to, fetching them through your logged-in real Chrome (macOS: it drives Chrome via
`osascript`, since headless browsers are Cloudflare-flagged). Set
`INSTITUTION_EBSCO_PROFILE` to your library's EBSCO cluster id for the EBSCO route.

**Sci-Hub is off by default** and only runs with an explicit `--scihub`. It hosts
copyrighted papers without publisher permission; legality depends on your jurisdiction and
it breaches most publishers' and institutions' terms either way. Enable at your own risk —
I wouldn't.

`SERPAPI_API_KEY` (Google Scholar tier, free tier is 100 searches/month) and
`RESEARCHER_EMAIL` (polite Unpaywall usage) are read from the environment.

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
