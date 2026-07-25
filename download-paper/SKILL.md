---
name: download-paper
description: Download academic PDFs by DOI for claim verification. Use when you need to read/verify the content of an academic paper and have its DOI. Downloads via Unpaywall, repository scraping, and Google Scholar (SerpAPI). Falls back to browser-use for downloading or reading paper content when automated download fails.
allowed-tools: Bash(python3*download_paper*), Bash(python3*institutional_fetch*), Bash(pdftotext*), Bash(*uvx browser-use*), Bash(*browser-use*), Bash(file *), Bash(ls *), Read
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

## Step 4: Use browser-use to download or read the paper

When the script fails, use `browser-use --browser real` (user's Chrome with login sessions) to try downloading the PDF, or read paper content from the page.

### 3a: Try downloading via browser-use

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

### 3b: If download still fails, read content from the page

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

### 3c: Always close the browser when done

```bash
uvx browser-use close
```

## Step 5: Paywalled papers the user's institution subscribes to (OpenAthens / EBSCO)

When the paper is paywalled but the user has institutional access, use
`institutional_fetch.py`. It fetches the PDF through the user's **logged-in real Chrome**
session — no scraping of rendered pages (which triggers publisher rate-limits).

**Prereq:** the user logs into the publisher (or EBSCO) once in `browser-use --browser real`
via their institution / OpenAthens. Then export the session:

```bash
python3 $SKILL_DIR/institutional_fetch.py export-cookies
```

**Direct publisher PDF (Springer, Wiley, Taylor & Francis, SAGE, Royal Society, PLOS, SSRN):**
```bash
python3 $SKILL_DIR/institutional_fetch.py --doi "10.1080/..." 2>/tmp/inst.log
```
It builds the publisher's canonical PDF URL and fetches it with the session cookies.

**EBSCOhost (APA PsycInfo, and Springer/Cloudflare-blocked psych papers EBSCO indexes):**
```bash
python3 $SKILL_DIR/institutional_fetch.py --title "Exact Article Title" --ebsco 2>/tmp/inst.log
```

Success prints the cached PDF path on stdout (same contract as `download_paper.py`).
Set `INSTITUTION_EBSCO_HOME` to your library's EBSCO entry URL (the
`research.ebsco.com/c/<cluster>/...` shown after you log in) — there is no sensible default,
the built-in one is the author's own library.

### Critical gotchas (learned the hard way)

- **Domain-aware cookies.** A real browser holds thousands of cookies. Sending all of them to one
  host returns **`400 Request Header Or Cookie Too Large`**. Build a per-domain `RequestsCookieJar`
  (set each cookie with its `domain`/`path`) so requests sends only the target host's cookies.
  `institutional_fetch.py` already does this; replicate it in any ad-hoc fetch.
- **One OpenAthens handshake per publisher.** A login at publisher A does not authenticate publisher B.
  Each publisher needs its own "Access through your institution" once; re-export cookies after each.
- **EBSCO is a Next.js / React app** — results are fetched client-side and are **not** in `browser-use state`
  (which shows only ~40 chrome controls) nor as `<a href>` links. Drive it with `browser-use eval`
  running JS that pierces shadow roots: React-safe fill of the search input (native value setter +
  Enter + `form.requestSubmit()`) → click **"Access options"** → click the **"PDF"** menu item →
  the PDF viewer's `performance.getEntriesByType("resource")` exposes the real bytes URL
  `https://content.ebscohost.com/cds/retrieve?content=<token>` → fetch THAT with cookies. The viewer's
  visible **Download button does not work**, and URL-param search (`?q=...`) does not execute — type into
  the box. (All encoded in `institutional_fetch.py fetch_ebsco`.)
- **Elsevier / ScienceDirect** PDFs are token-protected and not constructable; the reliable route is the
  authenticated-browser HTML of the article page (full text renders when logged in) — strip any
  browser-extension panels before using the text.
- **Driving opaque JS apps generally:** if `state` and DOM queries fail, fall back to `browser-use screenshot`
  + read the image + `browser-use click X Y` by coordinates.

## Finding the right URL

When the download script fails:
1. Check `/tmp/download_paper.log` for URLs found (e.g. `Found PDF via Google Scholar: ...`)
2. Academia.edu download URLs (`academia.edu/download/...`) redirect to the paper page — use either
3. Construct the DOI URL directly: `https://doi.org/DOI_HERE`

## Environment Variables

- `SERPAPI_API_KEY` — Required for Google Scholar tier. Set in `~/.zshrc`.
- `RESEARCHER_EMAIL` — Optional email for Unpaywall API.

## Cache

Downloaded PDFs are cached in `~/.claude/cache/pdfs/` (MD5 of DOI). Use `--force` to re-download.

## Known Limitations

- **Academia.edu and ResearchGate** often require login for PDF downloads. `browser-use --browser real` can access them if the user is logged in via Chrome.
- **Cloudflare-protected publishers** (SAGE, Elsevier) block headless browsers and curl. Use the
  logged-in real browser via Step 5 (`institutional_fetch.py` direct-PDF or EBSCO route); for Elsevier
  specifically, the authenticated article-page HTML is the reliable fallback.
- **SerpAPI** free tier: 100 searches/month.
