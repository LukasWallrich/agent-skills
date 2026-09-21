---
name: download-paper
description: Download academic PDFs by DOI for claim verification. Use when you need to read/verify the content of an academic paper and have its DOI. Downloads through the fetchpdf open-access chain (Unpaywall, PMC, preprint servers, repositories), a Google Scholar step, and the user's real signed-in Chrome for paywalled sources, checking every PDF against the requested DOI.
allowed-tools: Bash(*download_paper.py*), Bash(*institutional_fetch*), Bash(pdftotext*), Bash(*uvx browser-use*), Bash(*browser-use*), Bash(osascript*), Bash(curl*), Bash(file *), Bash(ls *), Read
---

# Download & Verify Academic Papers

`$SKILL_DIR` below is wherever this skill lives (e.g. `~/.claude/skills/download-paper`).

## Step 1: Try downloading the PDF

```bash
PDF_PATH=$($SKILL_DIR/download_paper.py --doi "DOI_HERE" 2>/tmp/download_paper.log)
```

Run the script directly — its shebang is `uv run --script`, which installs
[`fetchpdf`](https://github.com/The-Metascience-Observatory/fetchpdf) into a managed
environment on first use (a few seconds); later runs reuse uv's cache. The fetchpdf version
is pinned to a commit hash in the script's `# dependencies` header; edit that hash to update.

Sources, in order:

1. Cache (a copy of this DOI fetched earlier)
2. The fetchpdf open-access chain: OpenAlex, Unpaywall, PubMed Central, Europe PMC,
   Crossref links, Semantic Scholar, arXiv and other preprint servers, repository
   landing pages, publisher routes
3. Google Scholar via SerpAPI
4. The user's running, signed-in Chrome — one throwaway tab walks the Scholar links plain
   HTTP could not fetch, then the publisher's own PDF URLs (canonical URL by DOI prefix,
   `citation_pdf_url` from the landing page, ScienceDirect `/pdfft`), then EBSCOhost

Every downloaded PDF is checked against the requested DOI, title and page range. A
different paper, or a publisher preview of only the first page, is discarded and the walk
moves on to the next candidate.

**What the Chrome step needs** (it logs one line and skips when any of these is missing):

- macOS, with Google Chrome already running and at least one window open
- Chrome signed in to the publisher or EBSCO through the user's institution / OpenAthens
- *View ▸ Developer ▸ Allow JavaScript from Apple Events* switched on once
- `INSTITUTION_EBSCO_PROFILE` set to the library's EBSCO cluster id, for the EBSCO candidate

The step opens its tab at the end of the front window, never focuses it, and closes it
again on the way out, so it does not disturb what the user is doing.

Options:

- `--title "Paper Title"` — the Google Scholar query; otherwise taken from Crossref
- `--force` — re-download even if cached
- `--no-browser` — skip the Chrome step (leaves the run entirely non-interactive)
- `--playwright` — let fetchpdf render JS-heavy pages in a headless browser. Slower, and
  Cloudflare-protected publishers block it, so it is off by default.
- `--open` — on failure, open the best candidate URL in the user's browser for manual download

Exit codes: 0 = success (path on stdout), 1 = failure, 2 = opened in browser for manual download.

## Step 2: Read the downloaded PDF

```bash
pdftotext "$PDF_PATH" - | head -200           # First 200 lines
pdftotext "$PDF_PATH" - | grep -A5 "keyword"  # Search for specific claims
```

## Step 3: When the download fails

The script lists its candidate URLs on stderr, each with a note:

1. `[Scholar link, no PDF by plain HTTP]` — a PDF link Google Scholar offered that returned
   403, a bot check, or a landing page. The Chrome step tries these first when it runs.
2. `[Scholar link, a different paper]` — fetched, but not this DOI's paper. Skip it.
3. `[publisher page]` — `https://doi.org/DOI_HERE`, which honours institutional access

Three manual routes, in the order worth trying.

### 3a: Sign in at this publisher, then re-run

One OpenAthens handshake covers one publisher. A login at publisher A does not
authenticate publisher B, and the Chrome step can only use sessions that already exist.
Ask the user to open the `[publisher page]` URL, click *Access through your institution*,
and say when they are through; then re-run Step 1 with `--force`.

### 3b: Read the paper off the page

When no PDF exists to download (a landing page with the full text in HTML, an
Academia.edu or ResearchGate record), read the content instead. `browser-use --browser
real` drives the user's Chrome and returns structured page content — abstract, key
findings, figure captions, references — which is often enough for claim verification.

```bash
uvx browser-use --browser real open "PAPER_URL"
uvx browser-use state          # structured page content; repeat after `scroll down`
uvx browser-use close          # always close when done
```

`browser-use` is unreliable against heavy JS apps such as the EBSCO viewer: `state` and
`eval` come back empty there. Use it for ordinary publisher pages.

### 3c: Run one route on its own

`institutional_fetch.py` holds the Chrome layer Step 1 uses, and its CLI runs a single
route against one DOI — useful for debugging a route or for the cookie-jar route, which
Step 1 does not use.

```bash
$SKILL_DIR/institutional_fetch.py --doi "10.1037/edu0000827" --ebsco 2>/tmp/inst.log
$SKILL_DIR/institutional_fetch.py --doi "..." --ebsco --all-db   # beyond PsycInfo
$SKILL_DIR/institutional_fetch.py export-cookies                 # once, after logging in
$SKILL_DIR/institutional_fetch.py --doi "10.1080/..." 2>/tmp/inst.log   # cookie-jar route
```

Success prints the verified PDF's path on stdout (same contract as `download_paper.py`).

### Critical gotchas (learned the hard way)

- **Verify page 1 against the DOI or title for anything you fetch by hand.** Title-based
  search returns topically-similar papers under the DOI you asked for, and a mislabelled
  PDF silently corrupts whatever dataset it lands in. `download_paper.py` and
  `institutional_fetch.py` both run fetchpdf's identity check themselves; the `browser-use`
  route does not.
- **Navigate before you fetch.** Cross-origin fetch of a publisher PDF is CORS-blocked. The
  Chrome step navigates the tab to the PDF URL first, waits until
  `document.contentType == 'application/pdf'`, then fetches same-origin with credentials —
  the asset host (`pdf.sciencedirectassets.com`, silverchair, `content.ebscohost.com`) is
  same-origin once the tab is on it. The wait also lets Cloudflare, Anubis and Imperva
  interstitials clear themselves, which they do in a real browser and never under curl.
- **Chrome's PDF viewer cannot return a Promise to AppleScript.** The in-page fetch parks
  its base64 on `window.__pdf`; a poll collects it and reads it back in 800k-character
  chunks. A plain `return await fetch(...)` yields an empty string with no error.
- **Chrome's AppleScript tab `id` compares as a string.** `if (id of tt as string) is "…"`
  matches; a numeric `=` matches nothing and every command silently does nothing.
- **`Content-Disposition: attachment` URLs never reach the viewer.** Chrome drops the file
  in `~/Downloads` and the tab stays put, so the wait times out. Prefer the inline PDF URL
  of a pair (`/doi/pdf/…` over `/doi/pdfdirect/…?download=true`).
- **Signed EBSCO content URLs need no cookies.** `content.ebscohost.com/cds/retrieve?content=<token>`
  authenticates itself; plain `curl` downloads it. Don't rebuild the cookie dance for it.
- **EBSCO failure modes are informative.** No record id means EBSCO does not index the
  paper; a record with no content URL means EBSCO has "Linked Full Text" only — a link-out
  to the publisher, not a hosted PDF.
- **Domain-aware cookies.** A real browser holds thousands of cookies. Sending all of them to
  one host returns **`400 Request Header Or Cookie Too Large`**. The cookie-jar route builds
  a per-domain `RequestsCookieJar` so requests sends only the target host's cookies.
- **Off-campus IP gating is real.** Even in a logged-in real Chrome, SAGE / Wiley / T&F / OUP
  / IOS / Springer / BMJ direct routes are frequently campus-IP-gated. EBSCO is not. Keep a
  VPN / on-campus batch list rather than retrying.
- **Expect a low yield on paywalled batches.** One real batch: 62 paywalled papers → 8
  fetched. Plan around partial coverage.
- **The EBSCO viewer changes.** If the resource-timings lookup stops finding a
  `cds/retrieve` URL, check the viewer's network tab and the
  `research.ebsco.com/api/researcher-edge-aggregator/v1/records/<id>/fulltext/pdf?...&intent=download`
  JSON endpoint before rebuilding the flow.

## Environment Variables

Keys are read from the environment, then from `~/.claude/api_keys.env`.

- `SERPAPI_API_KEYS` (comma-separated) or `SERPAPI_API_KEY` — enables the Google Scholar
  step. Keys are tried in order; an exhausted or invalid key moves on to the next.
- `INSTITUTION_EBSCO_PROFILE` — the library's EBSCO cluster id, the `<cluster>` in the
  `research.ebsco.com/c/<cluster>/...` URL Chrome lands on after an institutional login.
  Enables the EBSCO candidate in the Chrome step.
- `CORE_API_KEY`, `ELSEVIER_API_KEY`, `OPENALEX_API_KEY` — optional; passed to fetchpdf's
  CORE, Elsevier text-mining and OpenAlex sources.
- `RESEARCHER_EMAIL` or `EMAIL` — contact address for the open-access APIs (Unpaywall
  requires one; Crossref gives a higher rate limit with one).

## Cache

Downloaded PDFs are cached in `~/.claude/cache/pdfs/` (MD5 of DOI). Use `--force` to re-download.
