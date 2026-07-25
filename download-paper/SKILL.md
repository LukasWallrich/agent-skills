---
name: download-paper
description: Download academic PDFs by DOI for claim verification. Use when you need to read/verify the content of an academic paper and have its DOI. Downloads via Unpaywall, repository scraping, and Google Scholar (SerpAPI). Falls back to the user's real signed-in Chrome for paywalled or JS-heavy sources when automated download fails.
allowed-tools: Bash(python3*download_paper*), Bash(python3*institutional_fetch*), Bash(pdftotext*), Bash(*uvx browser-use*), Bash(*browser-use*), Bash(osascript*), Bash(curl*), Bash(file *), Bash(ls *), Read
---

# Download & Verify Academic Papers

`$SKILL_DIR` below is wherever this skill lives (e.g. `~/.claude/skills/download-paper`).

## Step 1: Try downloading the PDF

```bash
PDF_PATH=$(python3 $SKILL_DIR/download_paper.py --doi "DOI_HERE" 2>/tmp/download_paper.log)
```

The script tries these sources in order:
1. Cache (previously downloaded)
2. Unpaywall API (open access direct PDF)
3. Repository landing page scraping (DSpace, HAL, institutional repos)
4. Google Scholar via SerpAPI (Academia.edu, ResearchGate, arXiv, etc.)
5. Playwright browser automation (JS-rendered pages)

Options:
- `--title "Paper Title"` — improves Google Scholar search (especially for short titles)
- `--force` — re-download even if cached
- `--scihub` — **off by default.** Adds a Sci-Hub tier (mirrors auto-discovered from
  Wikipedia). Sci-Hub hosts copyrighted papers without publisher permission; legality
  depends on your jurisdiction and it breaches most publishers' and institutions' terms
  either way. Enable at your own risk — I wouldn't. For paywalled papers your library
  subscribes to, Step 5 (`institutional_fetch.py`) is the legitimate route.
- `--open` — on failure, open the best candidate URL in the user's browser for manual download

Exit codes: 0 = success (path on stdout), 1 = failure, 2 = opened in browser for manual download.

## Step 2: Read the downloaded PDF

```bash
pdftotext "$PDF_PATH" - | head -200           # First 200 lines
pdftotext "$PDF_PATH" - | grep -A5 "keyword"  # Search for specific claims
```

## Step 3: If download fails, ask user if they want to download manually

If the script exits with code 1, ask the user: "Would you like me to open the paper in your browser so you can download it manually?" If yes, re-run with `--open`:

```bash
python3 $SKILL_DIR/download_paper.py --doi "DOI_HERE" --open 2>&1
```

This opens the best available URL (preferring doi.org for institutional access) in their default browser. The log output tells them where to save the PDF for caching.

## Step 4: Use the real browser to download or read the paper

When the script fails, drive the user's own Chrome (which holds their login sessions) to try
downloading the PDF, or to read the paper's content off the page.

`browser-use --browser real` is the convenient route and is fine for ordinary publisher
pages. It is **not** reliable against heavy JS apps like the EBSCO viewer — for those, and
for anything where `state`/`eval` come back empty, drive Chrome directly with `osascript`
(see Step 5, which does exactly that).

### 4a: Try downloading via browser-use

First, find the URL from the download log:
```bash
cat /tmp/download_paper.log | grep "Found PDF"
```

Then open the page and try to download:
```bash
# Open the paper page in user's real Chrome
uvx browser-use --browser real open "PAPER_URL"

# Check page state to find download buttons
uvx browser-use state
```

Look in the state output for download-related elements (buttons/links with text like "Download PDF", "Download Free PDF", "Save", or href containing "download" or ".pdf"). Then:

```bash
# Click the download button/link by its index
uvx browser-use click INDEX

# Wait a moment, then check ~/Downloads for the PDF
ls -lt ~/Downloads/*.pdf | head -3
```

If a PDF appeared in ~/Downloads, copy it to the cache:
```bash
cp ~/Downloads/FILENAME.pdf ~/.claude/cache/pdfs/
```

Then read it with `pdftotext` as in Step 2.

### 4b: If download still fails, read content from the page

If clicking download triggers a login modal or doesn't produce a PDF, read the paper content directly from the page state:

```bash
# The state output already contains structured content:
# - Abstract text
# - Key takeaways (on Academia.edu)
# - Figure captions and descriptions
# - References
uvx browser-use state

# Scroll down to see more content
uvx browser-use scroll down
uvx browser-use state
```

This is often sufficient for claim verification (abstract, key findings, figure captions).

### 4c: Always close the browser when done

```bash
uvx browser-use close
```

## Step 5: Paywalled papers the user's institution subscribes to (EBSCO / OpenAthens)

When the paper is paywalled but the user has institutional access, use
`institutional_fetch.py`. It works through the user's **real, signed-in Chrome** — headless
browsers are flagged by Cloudflare at most publishers, so there is no automated tier here.

**Prereq:** the user logs in once, in their normal Chrome, at the publisher or EBSCO via
their institution / OpenAthens. Chrome must also have
*View ▸ Developer ▸ Allow JavaScript from Apple Events* enabled (once). Set
`INSTITUTION_EBSCO_PROFILE` to your library's cluster id — the `<cluster>` in the
`research.ebsco.com/c/<cluster>/...` URL you land on after logging in.

**EBSCOhost — the best route for psychology** (APA PsycInfo, incl. all APA `apl****`, plus
Springer / Cloudflare-blocked papers EBSCO indexes). Searched **by DOI**:

```bash
python3 $SKILL_DIR/institutional_fetch.py --doi "10.1037/edu0000827" --ebsco 2>/tmp/inst.log
python3 $SKILL_DIR/institutional_fetch.py --doi "..." --ebsco --all-db   # beyond PsycInfo
```

The flow, driven through Chrome with `osascript` (macOS only):
`search/results?q=<doi>&db=psyh` → record id from a `/search/details/` link →
`viewer/pdf/<rid>` → the signed `content.ebscohost.com/cds/retrieve?content=<token>` URL from
`performance.getEntriesByType('resource')` → **plain `curl`** downloads it.

That signed URL is **self-authenticating** — no cookies, no CORS workaround, no browser
download plumbing. Failure modes are informative: no record id means EBSCO doesn't index it;
a record but no content URL means EBSCO has "Linked Full Text" only (a link-out, not a hosted
PDF).

**Direct publisher PDF (Springer, Wiley, Taylor & Francis, SAGE, Royal Society, PLOS, SSRN):**
```bash
python3 $SKILL_DIR/institutional_fetch.py export-cookies      # once, after logging in
python3 $SKILL_DIR/institutional_fetch.py --doi "10.1080/..." 2>/tmp/inst.log
```
It builds the publisher's canonical PDF URL and fetches it with a domain-aware cookie jar.

Success prints the cached PDF path on stdout (same contract as `download_paper.py`).

### Critical gotchas (learned the hard way)

- **Always verify page 1 against the DOI or title before saving.** Title-based tiers —
  Google Scholar / SerpAPI especially — return topically-similar papers under the DOI you
  asked for (7/7 wrong in one observed batch). A mislabelled PDF silently corrupts whatever
  dataset it lands in. `verify_pdf()` does this check; do it for anything you fetch by hand.
- **Signed EBSCO content URLs need no cookies.** Don't rebuild the cookie dance for them.
- **`nav_then_fetch` for publishers whose direct fetch 403s or returns HTML.** Cross-origin
  fetch is CORS-blocked, so navigate Chrome to the PDF URL first, wait, confirm
  `document.contentType == 'application/pdf'`, then fetch same-origin from that page — the
  asset host (`pdf.sciencedirectassets.com`, silverchair, `content.ebscohost.com`) is
  same-origin once you are on it. Wins this way: Elsevier ScienceDirect `/pdfft`, OUP
  `/article-pdf`.
- **Domain-aware cookies.** A real browser holds thousands of cookies. Sending all of them to
  one host returns **`400 Request Header Or Cookie Too Large`**. Build a per-domain
  `RequestsCookieJar` (each cookie set with its `domain`/`path`) so requests sends only the
  target host's cookies. `institutional_fetch.py` already does this.
- **One OpenAthens handshake per publisher.** A login at publisher A does not authenticate
  publisher B. Each needs its own "Access through your institution" once; re-export cookies
  after each.
- **Off-campus IP gating is real.** Even in a logged-in real Chrome, SAGE / Wiley / T&F / OUP
  / IOS / Springer / BMJ direct routes are frequently campus-IP-gated. EBSCO is not. Keep a
  VPN / on-campus batch list rather than retrying.
- **Expect a low yield.** One real batch: 62 paywalled papers → 8 fetched. Plan around
  partial coverage instead of assuming the pipeline will fill every gap.
- **`browser-use` is unreliable against these apps** (its `eval`/`state` routes broke against
  the EBSCO viewer); the working code drives Chrome via `osascript` instead. `browser-use` is
  still fine for ordinary pages — see Step 4.
- **The EBSCO viewer changes.** The old `linkprocessor/v2-pdf-full-text` selector, and the
  click-through "Access options" → "PDF" path, are both gone. If the resource-timings lookup
  stops finding a `cds/retrieve` URL, check the viewer's network tab and the
  `research.ebsco.com/api/researcher-edge-aggregator/v1/records/<id>/fulltext/pdf?...&intent=download`
  JSON endpoint before rebuilding the flow.

## Finding the right URL

When the download script fails:
1. Check `/tmp/download_paper.log` for URLs found (e.g. `Found PDF via Google Scholar: ...`)
2. Academia.edu download URLs (`academia.edu/download/...`) redirect to the paper page — use either
3. Construct the DOI URL directly: `https://doi.org/DOI_HERE`

## Environment Variables

- `SERPAPI_API_KEY` — Required for Google Scholar tier. Set in `~/.zshrc`.
- `RESEARCHER_EMAIL` — Optional email for Unpaywall API.
- `INSTITUTION_EBSCO_PROFILE` — Your library's EBSCO cluster id, for the Step 5 EBSCO route.

## Cache

Downloaded PDFs are cached in `~/.claude/cache/pdfs/` (MD5 of DOI). Use `--force` to re-download.

## Known Limitations

- **Academia.edu and ResearchGate** often require login for PDF downloads. `browser-use --browser real` can access them if the user is logged in via Chrome.
- **Cloudflare-protected publishers** (SAGE, Elsevier) block headless browsers and curl. Use the
  logged-in real browser via Step 5 (`institutional_fetch.py` direct-PDF or EBSCO route); for Elsevier
  specifically, the authenticated article-page HTML is the reliable fallback.
- **SerpAPI** free tier: 100 searches/month.
