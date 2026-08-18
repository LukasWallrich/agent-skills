---
name: upload-public
description: Upload a local file (screenshot, image, video, log, PDF) to a public Cloudflare R2 bucket and get a stable https URL plus a ready-to-paste markdown snippet. Use to embed an image in a GitHub issue, PR, comment, chat message, or any page that cannot read local paths.
---

# Upload a file to a public URL

Puts a file in your own Cloudflare R2 bucket and prints its public URL. Anyone with the
URL can read it; nothing is listed or indexed.

## Usage

```bash
python3 ~/.claude/skills/upload-public/upload.py <file> [<file> ...] [--prefix <name>] [--name <basename>]
python3 ~/.claude/skills/upload-public/upload.py --clipboard          # macOS clipboard image
python3 ~/.claude/skills/upload-public/upload.py recording.mov --gif  # video → embeddable GIF
python3 ~/.claude/skills/upload-public/upload.py --list
python3 ~/.claude/skills/upload-public/upload.py --delete <key>
```

The script prints, per file, the URL on one line and a markdown snippet on the next.
It fails loudly if the upload does not come back as HTTP 200, so a printed URL is a
working URL.

Options:

- `--prefix` groups objects under a folder, e.g. `--prefix flora` → `flora/2026/08/…`.
- `--name` sets the base filename (single file only). Default is the file's own stem.
- `--gif` converts a video to an animated GIF before upload; `--fps` and `--width` tune it.
- Keys are `[prefix/]YYYY/MM/<slug>-<8 hex chars>.<ext>`, where the hex is a hash of the
  file contents. Re-uploading the same file under the same name in the same month gives
  the same URL.

## What renders where

**Images embed everywhere — through markdown syntax.** GitHub rewrites `![alt](url)` to
its camo image proxy, so PNG, JPEG, GIF, WebP and SVG display inline in issues, PRs and
comments. Animated GIFs animate. Use markdown image syntax, not a raw `<img src="…">`
tag: only markdown gets the camo rewrite, and a direct external `<img>` is blocked.

**Video from this bucket does not play inline on GitHub.** GitHub's `media-src` policy
lists only GitHub-owned hosts, and there is no camo equivalent for video, so
`<video src="https://pub-….r2.dev/x.mp4">` shows a player that never loads. Files
dragged into the GitHub editor play inline because they land on a GitHub asset host, and
there is no public API for those uploads. For a screen recording, use `--gif`:

```bash
python3 ~/.claude/skills/upload-public/upload.py recording.mov --gif --fps 10 --width 900
```

This converts with ffmpeg and uploads the GIF, which embeds and animates through camo.
Lower `--fps` or `--width` if the file gets large; the script warns above 10 MB. When a
GIF is the wrong trade-off (long recording, audio matters), upload the video as-is and
post a plain link, or drag the file into the GitHub editor by hand. A GitHub release
asset is another host whose URLs can play inline, but check the rendering before
relying on it.

## Setup

Needs the `aws` CLI on PATH and five values in `~/.claude/api_keys.env`:

```
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
R2_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
R2_PUBLIC_BUCKET=<bucket name>
R2_PUBLIC_BASE=https://pub-<hash>.r2.dev
```

The first three come from an R2 API token in the Cloudflare dashboard. For the last two,
create the bucket and turn on its public URL — the enable command prints the base:

```bash
wrangler r2 bucket create <bucket name>
wrangler r2 bucket dev-url enable <bucket name>
```

Notes on the service:

- Free tier: 10 GB stored, no egress charge. The `r2.dev` domain is rate-limited by
  Cloudflare, which is irrelevant at personal volume but unsuitable for a public site.
  Attach a custom domain to the bucket if you outgrow that.
- Uploaded objects have a one-year immutable cache header, so a URL keeps working and
  stays fast. Nothing expires on its own — use `--list` and `--delete` to clear old files.

## Guardrails

- Do not upload anything confidential: participant data, credentials, unpublished
  manuscripts, anything under a data-sharing agreement. The URL is unguessable but public.
- Ask before uploading a file the user did not name.
