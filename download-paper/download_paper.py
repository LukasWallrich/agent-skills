#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fetchpdf @ git+https://github.com/The-Metascience-Observatory/fetchpdf@82a4c474178a3e4245da82542c3a25434674e48e",
#   "requests",
# ]
# ///
"""Download an academic PDF by DOI, and check that it is that DOI's paper.

Three steps:
  A. `fetchpdf.fetch_pdf` — the open-access chain (OpenAlex, Unpaywall, PMC,
     Crossref links, preprint servers, repository landing pages, publisher
     routes). Every PDF it writes is checked against the requested record.
  B. Google Scholar via SerpAPI, when step A found nothing and
     a SerpAPI key is available. Each PDF link is downloaded and accepted only if
     fetchpdf's identity check says it is the requested paper.
  C. The user's running, signed-in Chrome (macOS), when steps A and B found
     nothing. One throwaway tab walks the Scholar links plain HTTP could not
     fetch, then the publisher's own PDF URLs, then EBSCOhost. `--no-browser`
     skips it.

On success: prints the absolute path to the PDF on stdout, exits 0.
On failure: exits 1 (exit 2 with --open, after opening a candidate URL).
All diagnostic output goes to stderr.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import institutional_fetch as chrome

DEFAULT_CACHE_DIR = os.path.expanduser("~/.claude/cache/pdfs")
DEFAULT_EMAIL = "unpaywall@impactstory.org"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SCHOLAR_RESULTS = 5
KEY_FILE = os.path.expanduser("~/.claude/api_keys.env")
#: fetchpdf's names for keys the key file holds under other names.
FETCHPDF_KEY_NAMES = {"COREAPIKEY": "CORE_API_KEY",
                      "ELSEVIER_TDM_API_KEY": "ELSEVIER_API_KEY",
                      "OPENALEXAPIKEY": "OPENALEX_API_KEY"}

#: stdout is reserved for the result path. fetchpdf prints its progress there,
#: so every call into it runs with stdout redirected to this stream.
STDOUT = sys.stdout


def log(msg):
    print(msg, file=sys.stderr)


def clean_doi(doi):
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def doi_to_cache_path(doi, cache_dir):
    md5 = hashlib.md5(clean_doi(doi).encode()).hexdigest()
    return os.path.join(cache_dir, f"{md5}.pdf")


def validate_pdf(path):
    """Whether the file exists, is big enough to be a document, and is a PDF."""
    if not path or not os.path.exists(path) or os.path.getsize(path) < 1000:
        return False
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


def fetch_url(url, timeout=60, accept="application/pdf,*/*"):
    """Body bytes for a URL, or None when the host refuses or times out."""
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            return resp.read()
    except Exception as e:
        log(f"    fetch failed: {e}")
        return None


def crossref_metadata(doi):
    """Title, search title, author surnames and printed page range for a DOI.

    Crossref keeps the subtitle in its own field, so `title` alone is often the
    half of the title a search cannot find the paper by. `search_title` joins
    the two; the identity check keeps the bare `title`, which is what a PDF is
    guaranteed to print in one piece.
    """
    url = f"https://api.crossref.org/works/{quote(clean_doi(doi), safe='/')}"
    body = fetch_url(url, timeout=15, accept="application/json")
    if not body:
        return {}
    try:
        msg = json.loads(body).get("message", {})
    except ValueError:
        return {}
    title = (msg.get("title") or [None])[0]
    subtitle = (msg.get("subtitle") or [None])[0]
    return {
        "title": title,
        "search_title": f"{title}: {subtitle}" if title and subtitle else title,
        "authors": [a["family"] for a in msg.get("author", []) if a.get("family")],
        "pages": msg.get("page") or "",
    }


def verify_identity(path, doi, title, pages):
    """fetchpdf's verdict on whether the PDF at `path` is this DOI's paper."""
    with contextlib.redirect_stdout(sys.stderr):
        from fetchpdf.retrieval.pdf_identity import verify_pdf_identity
        from fetchpdf.retrieval.resolve import arxiv_id_from_doi

        return verify_pdf_identity(
            path, doi, title, pages=pages, arxiv_id=arxiv_id_from_doi(doi)
        )


# =============================================================================
# Step A: the fetchpdf chain
# =============================================================================

def fetchpdf_download(doi, tmp_path, email, use_playwright):
    """Run the fetchpdf chain into tmp_path. Returns True when it wrote a PDF."""
    with contextlib.redirect_stdout(sys.stderr):
        from fetchpdf import fetch_pdf

        written = fetch_pdf(
            doi,
            tmp_path,
            email=email,
            verbose=True,
            allow_xml_fallback=False,
            use_playwright=use_playwright,
        )
    # A path fetchpdf did not return is a file it declined to vouch for.
    return written is not None and validate_pdf(tmp_path)


# =============================================================================
# Step B: Google Scholar via SerpAPI
# =============================================================================

def build_scholar_query(title, authors):
    """A Scholar query: the title, plus two surnames when the title is short."""
    if not title:
        return None
    if authors and len(title) < 60:
        return f"{title} {' '.join(authors[:2])}"
    return title


def load_key_file():
    """KEY=value pairs from ~/.claude/api_keys.env; the environment wins."""
    keys = {}
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            for line in f:
                name, sep, value = line.strip().removeprefix("export ").partition("=")
                if sep and not name.startswith("#"):
                    keys[name] = value.strip("'\"")
    keys.update(os.environ)
    return keys


def serpapi_keys(keys):
    """SerpAPI keys to try, in order: SERPAPI_API_KEYS (comma-separated), then SERPAPI_API_KEY."""
    listed = keys.get("SERPAPI_API_KEYS", "").split(",") + [keys.get("SERPAPI_API_KEY", "")]
    return list(dict.fromkeys(k.strip() for k in listed if k.strip()))


def serpapi_search(query, api_keys):
    """The Scholar result JSON from the first key with searches left, or {}.

    SerpAPI answers an exhausted or invalid key with HTTP 429/401, or with an
    `error` field on a 200; either moves on to the next key.
    """
    for n, api_key in enumerate(api_keys, 1):
        params = urlencode({"engine": "google_scholar", "q": query,
                            "api_key": api_key, "num": SCHOLAR_RESULTS})
        log(f"  Querying SerpAPI (key {n} of {len(api_keys)})...")
        body = fetch_url(f"https://serpapi.com/search.json?{params}",
                         timeout=30, accept="application/json")
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        if data is None:
            continue
        error = str(data.get("error") or "")
        if "run out" in error.lower() or "api key" in error.lower():
            log(f"  SerpAPI key {n}: {error}")
            continue
        if error:
            log(f"  SerpAPI: {error}")      # e.g. Google returned no results
        return data
    return {}


def scholar_pdf_links(query, api_keys):
    """Every PDF link in the top Scholar results, in result order."""
    data = serpapi_search(query, api_keys)

    links = []
    for result in data.get("organic_results", [])[:SCHOLAR_RESULTS]:
        for resource in result.get("resources", []):
            link = resource.get("link")
            if link and resource.get("file_format", "").upper() == "PDF":
                links.append(link)
        if re.search(r"arxiv\.org/pdf/", result.get("link", "")):
            links.append(result["link"])
    # Deduplicate, keeping Scholar's order.
    return list(dict.fromkeys(links))


def scholar_download(links, tmp_path, doi, title, pages):
    """Walk the Scholar links, keeping the first that is the requested paper.

    Returns (accepted_url, blocked_urls, wrong_urls). `blocked` are links that
    did not yield PDF bytes (403, bot-check HTML, a landing page) — candidates
    for the signed-in Chrome route. `wrong` are PDFs of some other document.
    """
    blocked, wrong = [], []
    for url in links:
        log(f"  Scholar candidate: {url}")
        body = fetch_url(url)
        if not body or not body.startswith(b"%PDF-") or len(body) < 1000:
            blocked.append(url)
            continue
        with open(tmp_path, "wb") as f:
            f.write(body)
        verdict = verify_identity(tmp_path, doi, title, pages)
        if verdict.ok:
            log(f"    accepted: {verdict.reason}")
            return url, blocked, wrong
        log(f"    rejected ({verdict.state}): {verdict.reason}")
        wrong.append(url)
        os.unlink(tmp_path)
    return None, blocked, wrong


# =============================================================================
# Step C: the user's signed-in Chrome
# =============================================================================

JS_MENU_HINT = ("Chrome refuses JavaScript from Apple Events. Turn it on once under "
                "View ▸ Developer ▸ Allow JavaScript from Apple Events, "
                "then re-run. Skipping the Chrome step.")


def browser_candidates(tab, doi, blocked_links):
    """Yield (url, source) PDF candidates for the browser walk, in try order.

    Scholar's blocked links first (they are already known to hold this paper),
    then the publisher's canonical PDF URLs, then whatever the landing page
    advertises — `citation_pdf_url`, and ScienceDirect's `/pdfft` for a PII URL.
    """
    seen = set()
    for url in blocked_links:
        if url not in seen:
            seen.add(url)
            yield url, "scholar"
    for url in chrome.pdf_urls(doi):
        if url not in seen:
            seen.add(url)
            yield url, "publisher"
    for url in chrome.landing_pdf_urls(tab, doi):
        if url not in seen:
            seen.add(url)
            yield url, "landing page"


def accept_bytes(data, tmp_path, doi, title, pages, label):
    """Write `data` to tmp_path and keep it only if it is this DOI's paper."""
    if not data:
        log(f"    {label}: no PDF bytes")
        return False
    with open(tmp_path, "wb") as f:
        f.write(data)
    verdict = verify_identity(tmp_path, doi, title, pages)
    if verdict.ok:
        log(f"    {label}: fetched and verified")
        return True
    log(f"    {label}: rejected ({verdict.state}) {verdict.reason}")
    os.unlink(tmp_path)
    return False


def browser_download(doi, blocked_links, tmp_path, title, pages, ebsco_profile):
    """Walk the browser candidates in one throwaway tab. Returns the URL kept.

    The tab is opened at the end of the front window, never focused, and closed
    again whatever happens.
    """
    if sys.platform != "darwin":
        log("The Chrome step is macOS only, skipping")
        return None
    if not chrome.chrome_running():
        log("Google Chrome is not running, skipping the Chrome step")
        return None

    try:
        with chrome.ChromeTab() as tab:
            if not tab.javascript_allowed():
                log(JS_MENU_HINT)
                return None
            for url, source in browser_candidates(tab, doi, blocked_links):
                log(f"  Chrome candidate ({source}): {url}")
                if accept_bytes(chrome.nav_then_fetch(tab, url), tmp_path,
                                doi, title, pages, "browser fetch"):
                    return url
            if ebsco_profile:
                log("  Chrome candidate (EBSCOhost): searching by DOI")
                if accept_bytes(chrome.fetch_ebsco(tab, doi, ebsco_profile, all_db=True),
                                tmp_path, doi, title, pages, "EBSCOhost"):
                    return "EBSCOhost"
            else:
                log("  INSTITUTION_EBSCO_PROFILE is not set, skipping the EBSCO candidate")
    except chrome.ChromeError as e:
        log(f"  Chrome step stopped: {e}")
    return None


# =============================================================================
# Orchestration
# =============================================================================

def download_paper(doi, title=None, cache_dir=DEFAULT_CACHE_DIR, email=None,
                   force=False, use_playwright=False, use_browser=True,
                   ebsco_profile=""):
    """Returns (path, candidates). path is None on failure.

    `candidates` is an ordered list of (url, note) for manual download.
    """
    doi = clean_doi(doi)
    cache_path = doi_to_cache_path(doi, cache_dir)
    if not force and validate_pdf(cache_path):
        log("Found in cache")
        return cache_path, []

    os.makedirs(cache_dir, exist_ok=True)
    meta = crossref_metadata(doi)
    query_title = title or meta.get("search_title")
    candidates = []

    # A downloaded PDF is moved onto the cache path only once it is a valid
    # PDF, so an interrupted or refused download never stands in for the paper.
    handle, tmp_path = tempfile.mkstemp(dir=cache_dir, prefix="part-", suffix=".pdf")
    os.close(handle)
    os.unlink(tmp_path)

    blocked = []
    try:
        log(f"Fetching {doi} via fetchpdf...")
        if fetchpdf_download(doi, tmp_path, email, use_playwright):
            os.replace(tmp_path, cache_path)
            log("Downloaded via fetchpdf")
            return cache_path, candidates

        api_keys = serpapi_keys(load_key_file())
        query = build_scholar_query(query_title, meta.get("authors"))
        if api_keys and query:
            log(f"Searching Google Scholar for: {query[:80]}")
            links = scholar_pdf_links(query, api_keys)
            accepted, blocked, wrong = scholar_download(
                links, tmp_path, doi, meta.get("title") or title, meta.get("pages"))
            if accepted:
                os.replace(tmp_path, cache_path)
                log(f"Downloaded via Google Scholar: {accepted}")
                return cache_path, candidates
            candidates += [(u, "Scholar link, no PDF by plain HTTP") for u in blocked]
            candidates += [(u, "Scholar link, a different paper") for u in wrong]
        elif not api_keys:
            log("No SerpAPI key in the environment or the key file, skipping the Google Scholar step")
        else:
            log("No title available for the Google Scholar step")

        if use_browser:
            log("Trying the signed-in Chrome...")
            kept = browser_download(doi, blocked, tmp_path, meta.get("title") or title,
                                    meta.get("pages"), ebsco_profile)
            if kept:
                os.replace(tmp_path, cache_path)
                log(f"Downloaded via Chrome: {kept}")
                return cache_path, []
        else:
            log("--no-browser, skipping the Chrome step")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    candidates.append((f"https://doi.org/{doi}", "publisher page"))
    return None, candidates


def main():
    parser = argparse.ArgumentParser(
        description="Download an academic PDF by DOI, verified against the DOI.")
    parser.add_argument("--doi", required=True, help="DOI of the paper")
    parser.add_argument("--title", help="Paper title, for the Google Scholar step")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help=f"Cache directory (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument("--email", help="Contact email for the OA APIs")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    parser.add_argument("--playwright", action="store_true",
                        help="Let fetchpdf render JS-heavy pages in a headless browser")
    parser.add_argument("--no-browser", action="store_true",
                        help="Skip the step that fetches through the user's signed-in Chrome")
    parser.add_argument("--open", action="store_true",
                        help="On failure, open the publisher page in the browser")
    args = parser.parse_args()

    email = (args.email or os.environ.get("RESEARCHER_EMAIL")
             or os.environ.get("EMAIL") or DEFAULT_EMAIL)
    os.environ["EMAIL"] = email
    keys = load_key_file()
    for fetchpdf_name, file_name in FETCHPDF_KEY_NAMES.items():
        if keys.get(file_name):
            os.environ.setdefault(fetchpdf_name, keys[file_name])

    path, candidates = download_paper(
        doi=args.doi,
        title=args.title,
        cache_dir=args.cache_dir,
        email=email,
        force=args.force,
        use_playwright=args.playwright,
        use_browser=not args.no_browser,
        ebsco_profile=keys.get("INSTITUTION_EBSCO_PROFILE", ""),
    )

    if path:
        print(os.path.abspath(path), file=STDOUT)
        sys.exit(0)

    log("No verified PDF for this DOI.")
    for url, note in candidates:
        log(f"  - {url}  [{note}]")

    if args.open and candidates:
        best = next((u for u, _ in candidates if "doi.org/" in u), candidates[0][0])
        log(f"Opening in browser: {best}")
        import subprocess
        subprocess.run(["open", best])
        log(f"Save the PDF to: {os.path.abspath(doi_to_cache_path(args.doi, args.cache_dir))}")
        sys.exit(2)

    sys.exit(1)


if __name__ == "__main__":
    main()
