---
name: download-paper
description: Download academic PDFs by DOI for claim verification. Use when you need to read/verify the content of an academic paper and have its DOI. Downloads through the fetchpdf open-access chain (Unpaywall, PMC, preprint servers, repositories), Wiley and Springer Nature APIs when their keys are set, a Google Scholar step, and the user's real signed-in Chrome for paywalled sources; when a publisher bot-blocks free content, falls back to Wayback Machine captures, the T3 collaborative browser, and NCBI E-utilities — checking every PDF against the requested DOI.
allowed-tools: Bash(*download_paper.py*), Bash(*institutional_fetch*), Bash(*chrome_cdp_download.py*), Bash(pdftotext*), Bash(*uvx browser-use*), Bash(*browser-use*), Bash(osascript*), Bash(curl*), Bash(file *), Bash(ls *), Bash(pandoc*), Bash(sed *), t3-code_preview_*
---

# Download & Verify Academic Papers

> **Experimental, and your responsibility.** This skill explores possible routes to papers
> the user may already be entitled to read; it is not a supported or endorsed access method.
> Publisher terms, library licences and institutional policies can restrict automated access,
> systematic downloading or the use of a signed-in session, even when you have a subscription.
> Check the terms that apply to you before using the browser routes, and do not use them to get
> round a paywall you have no entitlement to. Agents: use the open-access routes first, and raise
> any doubt about permission with the user rather than working around it.

`$SKILL_DIR` below is wherever this skill lives (e.g. `~/.claude/skills/download-paper`).

## Step 1: Try downloading the PDF

```bash
PDF_PATH=$($SKILL_DIR/download_paper.py --doi "DOI_HERE" 2>/tmp/download_paper.log)
```

Run the script directly — its shebang is `uv run --script`, which installs
[`fetchpdf`](https://github.com/The-Metascience-Observatory/fetchpdf) and `ddgs` into a
managed environment on first use (a few seconds); later runs reuse uv's cache. The fetchpdf version
is pinned to a commit hash in the script's `# dependencies` header; edit that hash to update.

Sources, in order:

1. Cache (a copy of this DOI fetched earlier)
2. The fetchpdf open-access chain: OpenAlex, Unpaywall, PubMed Central, Europe PMC,
   Crossref links, Semantic Scholar, arXiv and other preprint servers, repository
   landing pages, publisher routes
3. Publisher APIs, each only with its key and only for that publisher's DOIs, otherwise
   skipped without a word. See *Publisher APIs* below.
4. Web search — the `ddgs` package (DuckDuckGo and other engines) for `<DOI> pdf`, then
   `"<title>" pdf`. Finds author homepages, course pages and untagged institutional
   repositories that the open-access indexes miss. Only `.pdf` links whose result title
   matches the paper's title are downloaded (at most 5 per query). It is free, so it runs
   before the SerpAPI quota is spent. `ddgs` scrapes search engines; a rate limit or
   failure only means this step found nothing, and the walk carries on. Searches are spaced
   ≥ 1.5 s apart across runs, and non-empty results are cached for a week.
5. Google Scholar via SerpAPI
6. The user's running, signed-in Chrome — one worker tab walks the Scholar links plain
   HTTP could not fetch, then the publisher's own PDF URLs (canonical URL by DOI prefix,
   `citation_pdf_url` from the landing page, ScienceDirect `/pdfft`), then EBSCOhost

Every downloaded PDF is checked against the requested DOI, title and page range. A
different paper, or a publisher preview of only the first page, is discarded and the walk
moves on to the next candidate.

**What the Chrome step needs** (it logs one line and skips when any of these is missing):

- macOS, with Google Chrome already running and at least one window open
- No second `Google Chrome.app` instance running (Playwright's own Chromium is fine)
- Chrome signed in to the publisher or EBSCO through the user's institution / OpenAthens
- *View ▸ Developer ▸ Allow JavaScript from Apple Events* switched on once
- `INSTITUTION_EBSCO_PROFILE` set to the library's EBSCO cluster id, for the EBSCO candidate

The step works in one tab of its own minimised Chrome window, marked by an
`about:blank#download-paper-worker` tab, and navigates from inside the page, because
AppleScript's `make new tab` and `set URL` restore a minimised window. Runs queue on a lock.
Apple Events reach only the newest `Google Chrome.app` instance, so the step waits while
another one is running, for example one Playwright launched with `executablePath`. Point
Playwright at its own Chromium to avoid stalling this.

Options:

- `--title "Paper Title"` — the Google Scholar query; otherwise taken from Crossref
- `--force` — re-download even if cached
- `--no-browser` — skip the Chrome step (leaves the run entirely non-interactive)
- `--playwright` — let fetchpdf render JS-heavy pages in a headless browser. Slower, and
  Cloudflare-protected publishers block it, so it is off by default.
- `--accept-xml` — stop at Springer's XML instead of looking further for a PDF
- `--open` — on failure, open the best candidate URL in the user's browser for manual download

Exit codes: 0 = success (PDF path on stdout), 1 = failure, 2 = opened in browser for manual
download, 3 = XML full text only, with `--accept-xml`. stdout only ever carries a verified PDF.

### Publisher APIs

**Wiley text-and-data-mining API** (`WILEY_TDM_TOKEN`; 10.1002, 10.1111, 10.1046, 10.1034,
10.1348, 10.1113). Returns a PDF, held to the same identity check as every other. Access
depends on entitlement: of 6 papers the rest of the chain had failed on, 1 came back and 5
answered 403 from both the box and the Mac. A 403 means "not entitled", so carry on to the
next route. Requests are spaced 10 s apart across runs.

**Springer Nature Open Access API** (`SPRINGER_OA_API_KEY`; 10.1007, 10.1038, 10.1057,
10.1186, 10.3758). Returns JATS XML, never a PDF, and only for open-access articles; a 404 is
its normal answer for anything else, and fetchpdf usually finds those articles first, so expect
few hits (0 of 28 on one batch of failed papers). 401, 403 and 429 mean the key or the 500-a-day
quota, and switch the route off for the process. An article is kept only if it carries the
requested DOI, the title and a full-text body. It is cached as `<md5 of DOI>.jats.xml`, and
stderr gets the line `FULLTEXT_XML=<path>`.

By default the walk carries on looking for a PDF, and exits 1 if none turns up, with the XML
still on disk. Pass `--accept-xml` when text is enough to read the claim. The walk then stops
before the search and Chrome steps and exits 3. Read the result with:

```bash
XML=$(sed -n 's/^FULLTEXT_XML=//p' /tmp/download_paper.log | head -1)
pandoc -f jats -t plain --standalone "$XML"     # the reference list is in the XML itself; pandoc drops it
```

**Elsevier** (`ELSEVIER_API_KEY`) runs inside fetchpdf. From this network it seems to return only
first-page PDFs and abstract XML (unconfirmed), which the identity check refuses as truncated.

## Step 1b: Bot-blocked but free — three automated routes

When Step 1 fails, first ask whether the paper is actually free: OpenAlex (`api.openalex.org/works/doi:...`,
`oa_status`, `locations`) or a "Free access" marker on the landing page. A 403 on a free
article is bot-detection on this host's IP, not a paywall — and fetchpdf's chain terminates
at those same blocked publisher endpoints, so it will keep failing too. OpenAlex
`locations` also lists repository alternates worth trying directly. Then run, in order:

### Wayback first

```bash
curl -s 'https://archive.org/wayback/available?url=journals.sagepub.com/doi/pdf/10.1177/XXXX'   # availability API survives; CDX gets rate-limited
curl -sL -o paper.pdf 'http://web.archive.org/web/<TS>id_/<SNAPSHOT_URL>'                        # id_ = original bytes, no toolbar
```

Query the exact publisher PDF URL *and* its variants (`/doi/epdf/…`, `/doi/pdfdirect/…`,
repository `bitstream` URLs from OpenAlex locations, blog permalinks). One caveat: a
snapshot of a "PDF" URL can be an interstitial page (PMC's "Preparing to download" page),
so check the bytes, not just the snapshot's existence. Real wins: Sage AMPPS, MDPI,
university-repository bitstreams, blog posts.

The availability API answers HTTP 429 under sustained use, and its body then says nothing about
the URL. Space requests a few seconds apart, back off on a 429, and count that URL as unchecked,
never as "no snapshot". A bulk script that hid these errors reported a false "0 of 87".

### Collaborative browser (T3 `preview_*` tools)

This is an ordinary browser session, which some publishers serve when they refuse curl and
headless Playwright. Sage loads in seconds; OUP's browser check loops forever, so don't grind
that one. Recipe:

1. `preview_navigate` to the article landing page (same origin as the PDF).
2. `preview_evaluate`: same-origin `fetch(pdfUrl, {credentials:'include'})` →
   `URL.createObjectURL(new Blob(...))` → `<a download>` click.
3. `preview_status` → `downloads[]` → copy the local `path` on the host; verify page 1.

Two dead ends, verified the hard way — the download-manager click is the only practical
byte exfil: the renderer cannot reach loopback (`fetch` to `http://127.0.0.1:<port>` fails
`net::ERR_FAILED` even with correct CORS/PNA headers on the relay), and `preview_evaluate`
returns at most ~60 KB per call, so slicing anything bigger than a page is hopeless.

### PMC full text without the PMC website

`pmc.ncbi.nlm.nih.gov` reCAPTCHA-blocks datacenter IPs even in a real browser; Europe PMC's
renderer 403s curl and fails quietly from JS (a 57-byte `Failed to retrieve PDF for
pmcid:…` — check small downloads); `pmc/utils/oa/oa.fcgi` is dead (404). The API still works:

```bash
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=<PMCID>&rettype=xml' -o paper.xml
pandoc -f jats -t html5 --standalone --metadata title="..." paper.xml -o paper.html
```

Pandoc's JATS reader silently drops the back matter — append the `<ref-list>` from the XML
yourself.

## Step 1c: Paywalled PDFs — one command, no exclusive Chrome

When the paper is not free and the Chrome step in Step 1 stalls or is skipped, use:

```bash
$SKILL_DIR/chrome_cdp_download.py --doi "10.1016/j.lindif.2026.102967" --out ./pdfs
```

Why this beats the osascript route: Apple Events reach only the newest
`Google Chrome.app` instance, so one Playwright-launched Chrome anywhere on the machine
blocks it. This script instead copies `Local State` + `Default/Cookies` into a throwaway
`--user-data-dir`, launches that as its own offscreen instance with `--remote-debugging-port`,
and drives it with Playwright over CDP. Chrome itself decrypts the cookies (Chrome 127+
jar values cannot be decrypted in Python) and the user's window is untouched.

It does not run alongside the osascript route: while it runs, its Chrome is the newest
instance, so Step 1's Chrome step and `institutional_fetch.py` cannot reach the user's
Chrome. Do not start them at the same time.

How it gets in: the **OpenAthens redirector** is a universal institutional switch —

```
https://go.openathens.net/redirector/<institution-domain>?url=<article-or-doi-url>
```

Set `INSTITUTION_OPENATHENS_DOMAIN` (e.g. `bbk.ac.uk`) or pass `--institution`. One hop
authenticates the session on the publisher domain; no per-publisher *Access through your
institution* dialog. It works on Elsevier, T&F, Wiley, OUP, AAAS, MIT Press and SAGE —
**subject to the institution's entitlement**, which is the real limit (see Step 3b).

How it gets the bytes: navigate to the PDF URL and read the PDF viewer's own response
(`page.on('response')` → `response.body()`), or the download event. Do **not** use
`ctx.request.get` or same-origin `fetch` for publisher PDFs — publishers often answer them
with a 403 or an HTML check page even with valid cookies.

Every PDF goes through the same identity check as Step 1, against the Crossref title. A
DOI that is not in Crossref needs `--title`, which works with a single `--doi` only.

**Batches stop at 30 publisher fetches per run** (`--max-fetches`). Library licences often
prohibit systematic downloading, and a publisher can respond by blocking the institution, so
report the cap to the user rather than raising it or looping to get round it. PDFs already in
`--out` are re-checked and do not count.

A failed DOI's log says whether it stopped at a sign-in page (the Chrome session is missing
or expired) or reached the publisher with no PDF (likely no entitlement).

Options: `--doi-file dois.txt`, `--profile "Profile 1"`, `--springer-wayf`,
`--institution-name`, `--keep-profile`. `--no-verify` skips the identity check, so its
output needs a hand check. Exit 0 only if every DOI produced a file.

### Springer's own picker (only if entitled)

`wayf.springernature.com/?redirect_uri=<article>` needs real Playwright clicks:
`#autocomplete-input` → type the institution name → click
`.eds-c-autocomplete__item >> nth=0` → `#institution-submit`. `.click()` on the item with a
plain JS `element.click()` does nothing; use Playwright's click. After it lands, a page
still showing *Buy article* means the institution has no entitlement for that journal —
stop, it is not a flow bug. `--springer-wayf` does this with `INSTITUTION_NAME` or
`--institution-name`.

## Step 2: Read the downloaded PDF

```bash
pdftotext "$PDF_PATH" - | head -200           # First 200 lines
pdftotext "$PDF_PATH" - | grep -A5 "keyword"  # Search for specific claims
```

## Step 3: When the download fails

The script lists its candidate URLs on stderr, each with a note:

1. `[Scholar link, no PDF by plain HTTP]` — a PDF link Google Scholar offered that returned
   403, a bot check, or a landing page. The Chrome step tries these first when it runs.
   `[web-search link, no PDF by plain HTTP]` is the same for a title-matched web-search hit,
   which is not yet known to be this paper.
2. `[Scholar link, a different paper]` — fetched, but not this DOI's paper. Skip it.
3. `[publisher page]` — `https://doi.org/DOI_HERE`, which honours institutional access

Four manual routes (Step 1b's automated routes come first for free content; these are for
paywalled papers and truly unreachable copies), in the order worth trying.

### 3a: Get the institution's session into Chrome

`chrome_cdp_download.py` needs no per-publisher sign-in: the OpenAthens redirector above
authenticates any publisher domain in one hop, provided Chrome is signed in to OpenAthens
at all. If its log reports a sign-in page, ask the user to sign in to OpenAthens in Chrome.

Step 1's Chrome step does not use the redirector, and sessions the script opens stay in its
throwaway copy. For that step, one login still covers one publisher: ask the user to open
the `[publisher page]` URL, click *Access through your institution* and say when they are
through; then re-run Step 1 with `--force`.

If a DOI still lands on "Buy article" / "Pay-per-view" after signing in, the institution
does not subscribe to that journal (or that year), and another sign-in will not change it.
Check the library's A–Z list before spending time.

### 3b: When the institution simply has no entitlement

Expect this to be the common outcome for a recent-paper batch, and say so plainly rather
than retrying flows. Patterns seen in one real batch of 93:

- **Springer (10.1007/10.1057)** and **Emerald (10.1108)**: pages show *Buy article* /
  *Pay-Per-View* even after OpenAthens. Many UK institutions lost or never had the
  Springer deal.
- **SAGE (10.1177)**: the session can say *I have access via: <institution>* and still
  return 403 for `/doi/pdf/…` and redirect `/doi/epdf/…` to `/doi/abs/`. Google Scholar's
  CASA hop does not change this. Treat SAGE PDFs as frequently unavailable even when HTML
  full text is not.
- **Cambridge (10.1017)**, **OUP handbook chapters (10.1093/oxfordhb, 10.1093/oso)**:
  abstract-only for non-subscribed titles.

`requests` + the exported cookie jar is **not** a fallback: SAGE, Wiley and T&F answer 403
with an HTML check page even with valid session cookies. Keep the download in the browser.

### 3c: Read the paper off the page

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

### 3d: Run one route on its own

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

- **Apple Events only reach the newest `Google Chrome.app`.** One Playwright-launched Chrome
  anywhere on the machine makes `institutional_fetch.py` wait (now capped at 2 minutes,
  with an error naming `chrome_cdp_download.py`) even though the real profile is idle.
  Use the CDP route rather than hunting for the offending process.
- **A non-Latin title cannot be identity-checked by title words.** Arabic, Chinese and
  Cyrillic titles normalise to almost nothing, so a 0% title match is not evidence the PDF
  is wrong. The identity check can then refuse the right paper: re-run
  `chrome_cdp_download.py` with `--no-verify` and read page 1 by hand.
- **Verify page 1 against the DOI or title for anything you fetch by hand.** Title-based
  search returns topically-similar papers under the DOI you asked for, and a mislabelled
  PDF silently corrupts whatever dataset it lands in. `download_paper.py` and
  `institutional_fetch.py` both run fetchpdf's identity check themselves, and also refuse a
  PDF whose only evidence is the printed DOI when its front pages carry under 70% of the
  title — a paper citing the DOI, or the article's supplement, prints it too
  ([fetchpdf#5](https://github.com/The-Metascience-Observatory/fetchpdf/issues/5)); the `browser-use`
  and Wayback routes do not — an archived "PDF" URL may capture an interstitial page, so
  confirm the bytes (`%PDF` magic, `pdftotext -l 1`) as well as the identity.
- **Navigate before you fetch.** Cross-origin fetch of a publisher PDF is CORS-blocked. The
  Chrome step navigates the tab to the PDF URL first, waits until
  `document.contentType == 'application/pdf'`, then fetches same-origin with credentials —
  the asset host (`pdf.sciencedirectassets.com`, silverchair, `content.ebscohost.com`) is
  same-origin once the tab is on it. The wait also covers publishers' browser-check pages,
  which finish on their own in a normal browser and never under curl.
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
- `INSTITUTION_OPENATHENS_DOMAIN` — the OpenAthens redirector slug, e.g. `bbk.ac.uk`
  (not the publisher URL). Enables `chrome_cdp_download.py`'s one-hop institutional
  access on every publisher domain. `--institution` overrides it.
- `INSTITUTION_NAME` — the institution's display name for Springer's WAYF picker
  (e.g. `Birkbeck College`); only used with `--springer-wayf`. `--institution-name` overrides it.
- `WILEY_TDM_TOKEN`, `SPRINGER_OA_API_KEY` — enable the publisher APIs. `SPRINGER_META_API_KEY`
  gives metadata only, and this skill does not use it.
- `CORE_API_KEY`, `ELSEVIER_API_KEY`, `OPENALEX_API_KEY` — optional; passed to fetchpdf's
  CORE, Elsevier text-mining and OpenAlex sources.
- `RESEARCHER_EMAIL` or `EMAIL` — contact address for the open-access APIs (Unpaywall
  requires one; Crossref gives a higher rate limit with one).

## Cache

Downloaded PDFs and Springer's XML are cached in `~/.claude/cache/pdfs/` (MD5 of DOI). Use
`--force` to re-download.
Web-search results live in `~/.claude/cache/pdfs/web-search/` (MD5 of the query) for a week;
delete that folder to search afresh.
