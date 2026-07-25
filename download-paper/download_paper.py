#!/usr/bin/env python3
"""Download academic PDFs given a DOI, with caching and multi-source fallback.

Tiered approach:
  1. Cache check
  1b. OSF preprints (direct download)
  2. Unpaywall direct PDF URL
  3. Unpaywall landing page -> scrape for PDF links
  4. Google Scholar via SerpAPI -> free PDF links
  5. Sci-Hub — OFF by default, opt in with --scihub (see the note at that tier)

On success: prints absolute path to PDF on stdout, exits 0.
On failure: prints error to stderr, exits 1.
All diagnostic output goes to stderr.
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_CACHE_DIR = os.path.expanduser("~/.claude/cache/pdfs")
DEFAULT_EMAIL = "unpaywall@impactstory.org"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def log(msg):
    print(msg, file=sys.stderr)


# =============================================================================
# Utilities
# =============================================================================

def clean_doi(doi):
    """Strip common DOI prefixes."""
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def doi_to_cache_path(doi, cache_dir):
    """Generate cache file path from DOI using MD5 hash."""
    md5 = hashlib.md5(clean_doi(doi).encode()).hexdigest()
    return os.path.join(cache_dir, f"{md5}.pdf")


def http_get(url, headers=None, timeout=30):
    """Fetch URL, return (body_bytes, final_url) or raise."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        ctx = ssl.create_default_context()
    except Exception:
        ctx = None
    resp = urlopen(req, timeout=timeout, context=ctx)
    return resp.read(), resp.url


def http_get_json(url, timeout=30):
    """Fetch URL and parse JSON response."""
    body, _ = http_get(url, headers={"Accept": "application/json"}, timeout=timeout)
    return json.loads(body)


def validate_pdf(path):
    """Check that a file looks like a valid PDF."""
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 1000:
        return False
    with open(path, "rb") as f:
        magic = f.read(5)
    return magic == b"%PDF-"


def download_pdf(url, output_path, referer=None, max_retries=2, timeout=60):
    """Download a PDF to output_path. Returns True on success."""
    headers = {"Accept": "application/pdf,*/*"}
    if referer:
        headers["Referer"] = referer

    for attempt in range(max_retries + 1):
        try:
            body, final_url = http_get(url, headers=headers, timeout=timeout)

            # Write to file
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(body)

            # Validate
            if validate_pdf(output_path):
                return True
            else:
                os.unlink(output_path)
                log(f"  Downloaded file from {url} is not a valid PDF")
                return False

        except (HTTPError, URLError, TimeoutError, OSError) as e:
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                log(f"  Retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                log(f"  Download failed: {e}")
                return False

    return False


# =============================================================================
# Tier 1b: OSF Preprints
# =============================================================================

def get_osf_pdf_url(doi):
    """Check if DOI is an OSF preprint and return direct download URL."""
    doi_clean = clean_doi(doi)
    m = re.match(r"^10\.3123[45]/osf\.io/(.+)$", doi_clean)
    if m:
        return f"https://osf.io/download/{m.group(1)}/"
    return None


# =============================================================================
# Tier 2-3: Unpaywall
# =============================================================================

def get_unpaywall_info(doi, email):
    """Query Unpaywall API. Returns dict with pdf_url and/or landing_url."""
    doi_clean = clean_doi(doi)
    url = f"https://api.unpaywall.org/v2/{quote(doi_clean, safe='/')}?email={quote(email)}"

    try:
        data = http_get_json(url)
    except Exception as e:
        log(f"  Unpaywall API error: {e}")
        return {}

    result = {}

    # Check best_oa_location first
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        result["pdf_url"] = best["url_for_pdf"]
        return result

    # Check all oa_locations
    for loc in data.get("oa_locations") or []:
        if loc.get("url_for_pdf"):
            result["pdf_url"] = loc["url_for_pdf"]
            return result

    # No direct PDF — collect landing pages from repositories
    for loc in data.get("oa_locations") or []:
        if loc.get("url"):
            result.setdefault("landing_urls", []).append(loc["url"])

    # Also store the title for Google Scholar fallback
    if data.get("title"):
        result["title"] = data["title"]

    return result


def extract_pdf_from_landing_page(landing_url):
    """Scrape a repository landing page for PDF download links."""
    try:
        body, final_url = http_get(
            landing_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=30,
        )
        html = body.decode("utf-8", errors="replace")
    except Exception as e:
        log(f"  Failed to fetch landing page {landing_url}: {e}")
        return None

    parsed = urlparse(final_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    pdf_links = []

    # Pattern 1: Direct .pdf links
    pdf_links += re.findall(r'href="([^"]+\.pdf[^"]*)"', html, re.IGNORECASE)

    # Pattern 2: HAL /document endpoint
    pdf_links += re.findall(r'href="([^"]+/document)"', html)

    # Pattern 3: DSpace bitstream links
    pdf_links += re.findall(r'href="([^"]+/bitstream/[^"]+)"', html)

    # Pattern 4: Pure/institutional repository file links
    pdf_links += re.findall(r'href="([^"]+/files?/[^"]+\.pdf[^"]*)"', html, re.IGNORECASE)

    # Pattern 5: Generic download links with PDF
    pdf_links += re.findall(r'href="([^"]*download[^"]*\.pdf[^"]*)"', html, re.IGNORECASE)

    if not pdf_links:
        return None

    # Resolve relative URLs
    resolved = []
    for link in pdf_links:
        if link.startswith("http"):
            resolved.append(link)
        elif link.startswith("//"):
            resolved.append(f"https:{link}")
        elif link.startswith("/"):
            resolved.append(f"{base_url}{link}")
        else:
            resolved.append(urljoin(final_url, link))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for link in resolved:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    # Filter out supplementary materials
    main = [
        l for l in unique
        if not re.search(r"(?i)supplement|appendix|supp_|_s\d", l)
    ]

    return (main or unique)[0]


# =============================================================================
# Tier 4: Google Scholar via SerpAPI
# =============================================================================

def search_google_scholar(title, api_key):
    """Search Google Scholar for free PDF links using SerpAPI."""
    if not api_key:
        log("  No SERPAPI_API_KEY set, skipping Google Scholar")
        return None

    params = urlencode({
        "engine": "google_scholar",
        "q": title,
        "api_key": api_key,
        "num": 5,
    })
    url = f"https://serpapi.com/search.json?{params}"

    try:
        data = http_get_json(url, timeout=30)
    except Exception as e:
        log(f"  SerpAPI error: {e}")
        return None

    # Look through organic results for PDF resources
    pdf_links = []
    for result in data.get("organic_results", []):
        # Check resources array (SerpAPI marks PDFs explicitly)
        for resource in result.get("resources", []):
            if resource.get("file_format", "").upper() == "PDF":
                pdf_link = resource.get("link")
                if pdf_link:
                    log(f"  Found PDF via Google Scholar: {pdf_link}")
                    pdf_links.append(pdf_link)

        # Check if the result link itself points to a known open-access host
        link = result.get("link", "")
        if re.search(r"arxiv\.org/pdf/", link):
            log(f"  Found arXiv PDF: {link}")
            pdf_links.append(link)

    return pdf_links


# =============================================================================
# Tier 4b: Browser-based PDF download (Playwright)
# =============================================================================

def download_pdf_via_browser(url, output_path, timeout=30):
    """Download a PDF using headless Chrome via Playwright.

    Handles sites that require JS rendering to find/access PDF links.
    Falls back gracefully if Playwright is not installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("  Playwright not available, skipping browser download")
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                accept_downloads=True,
                user_agent=USER_AGENT,
            )
            page = context.new_page()

            # Navigate to the URL (follows redirects)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            time.sleep(2)

            final_url = page.url

            # Strategy 1: Check if we landed on a PDF
            content_type = page.evaluate("() => document.contentType")
            if content_type and "pdf" in content_type.lower():
                pdf_bytes = page.evaluate(
                    """() => {
                        const xhr = new XMLHttpRequest();
                        xhr.open('GET', window.location.href, false);
                        xhr.responseType = 'arraybuffer';
                        xhr.send();
                        return Array.from(new Uint8Array(xhr.response));
                    }"""
                )
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(bytes(pdf_bytes))
                browser.close()
                if validate_pdf(output_path):
                    return True
                os.unlink(output_path)

            # Strategy 2: Find PDF links in rendered HTML and download via requests
            html = page.content()
            browser.close()

            # Extract PDF links from rendered page
            pdf_links = []
            pdf_links += re.findall(r'href="([^"]+\.pdf[^"]*)"', html, re.IGNORECASE)
            pdf_links += re.findall(r'href="([^"]+/bitstream/[^"]+)"', html)
            pdf_links += re.findall(r'href="([^"]*download[^"]*\.pdf[^"]*)"', html, re.IGNORECASE)

            parsed = urlparse(final_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            for link in pdf_links:
                if link.startswith("http"):
                    abs_link = link
                elif link.startswith("//"):
                    abs_link = f"https:{link}"
                elif link.startswith("/"):
                    abs_link = f"{base_url}{link}"
                else:
                    abs_link = urljoin(final_url, link)

                if re.search(r"(?i)supplement|appendix", abs_link):
                    continue

                if download_pdf(abs_link, output_path):
                    return True

            return False

    except Exception as e:
        log(f"  Browser download failed: {e}")
        return False


# =============================================================================
# Tier 5: Sci-Hub — opt-in only (--scihub)
#
# Sci-Hub hosts copyrighted papers without the publishers' permission. Whether
# using it is lawful depends on where you are, and it is against the terms of
# most publishers and many institutions either way. It is off by default.
# Enable at your own risk — I wouldn't. The legitimate routes above (Unpaywall,
# repositories, preprint servers) plus institutional_fetch.py cover most papers.
# =============================================================================

def get_scihub_mirrors():
    """Get current Sci-Hub mirror URLs from Wikipedia."""
    try:
        body, _ = http_get(
            "https://en.wikipedia.org/wiki/Sci-Hub",
            timeout=15,
        )
        html = body.decode("utf-8", errors="replace")
        # Look for sci-hub URLs in the page
        mirrors = re.findall(r'https?://sci-hub\.[a-z.]{2,10}', html)
        # Deduplicate preserving order
        seen = set()
        unique = []
        for m in mirrors:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        if unique:
            log(f"  Found {len(unique)} Sci-Hub mirrors from Wikipedia: {unique}")
            return unique
    except Exception as e:
        log(f"  Could not fetch Sci-Hub mirrors from Wikipedia: {e}")
    return []


# Known-working mirrors (verified 2026-05). The Wikipedia list is frequently
# stale: sci-hub.se can be unreachable and sci-hub.ee returns 403, while .st/.ru
# 302-redirect to .sg, and .sg/.wf serve PDFs directly. We always try these first,
# then append any extra mirrors scraped from Wikipedia.
SCIHUB_KNOWN_GOOD = [
    "https://sci-hub.st", "https://sci-hub.ru", "https://sci-hub.sg",
    "https://sci-hub.se", "https://sci-hub.wf",
]
SCIHUB_FALLBACK_MIRRORS = SCIHUB_KNOWN_GOOD

# Module-level cache so mirrors are looked up at most once per session
_scihub_mirrors_cache = None


def get_scihub_pdf_url(doi, mirrors=None):
    """Get PDF URL from Sci-Hub mirrors."""
    global _scihub_mirrors_cache
    if mirrors is None:
        if _scihub_mirrors_cache is None:
            # Known-good mirrors first, then any extra Wikipedia mirrors, deduped.
            merged = list(SCIHUB_KNOWN_GOOD)
            for m in get_scihub_mirrors():
                if m not in merged:
                    merged.append(m)
            _scihub_mirrors_cache = merged
        mirrors = _scihub_mirrors_cache

    doi_clean = clean_doi(doi)

    for mirror in mirrors:
        try:
            page_url = f"{mirror}/{doi_clean}"
            body, final_url = http_get(page_url, timeout=30)
            html = body.decode("utf-8", errors="replace")

            if "article is not available" in html:
                continue

            # Pattern 1: <object data="...pdf">
            m = re.search(r'<object[^>]+data\s*=\s*["\']([^"\'#]+\.pdf)', html)
            # Pattern 2: fetch() call
            if not m:
                m = re.search(r"fetch\(['\"]([^'\"]+\.pdf)", html)
            # Pattern 3: embed/iframe src
            if not m:
                m = re.search(r'(?:embed|iframe)[^>]+src=["\']([^"\'#]+\.pdf)', html)
            # Pattern 4: download-button href / location.href (sci-hub.sg style)
            if not m:
                m = re.search(r'(?:href|location(?:\.href)?)\s*=\s*["\']([^"\'#]+\.pdf)', html)

            if m:
                pdf_url = m.group(1)
                # Resolve relative URLs against the FINAL (post-redirect) host —
                # mirrors like .st/.ru redirect to .sg, so the original `mirror`
                # would otherwise point at the wrong host.
                host_m = re.match(r'https?://[^/]+', final_url or page_url)
                host = host_m.group(0) if host_m else mirror
                if pdf_url.startswith("//"):
                    pdf_url = f"https:{pdf_url}"
                elif pdf_url.startswith("/"):
                    pdf_url = f"{host}{pdf_url}"
                elif not pdf_url.startswith("http"):
                    pdf_url = f"{host}/{pdf_url}"
                return pdf_url, page_url

        except Exception:
            pass

        time.sleep(0.5)

    return None, None


# =============================================================================
# Orchestration
# =============================================================================

def get_metadata_from_crossref(doi):
    """Fetch paper title and authors from Crossref API."""
    doi_clean = clean_doi(doi)
    url = f"https://api.crossref.org/works/{quote(doi_clean, safe='/')}"
    try:
        data = http_get_json(url, timeout=15)
        msg = data.get("message", {})
        title = (msg.get("title") or [None])[0]
        authors = []
        for a in msg.get("author", []):
            family = a.get("family", "")
            if family:
                authors.append(family)
        return {"title": title, "authors": authors}
    except Exception as e:
        log(f"  Crossref lookup failed: {e}")
    return {}


def build_scholar_query(title, authors=None):
    """Build a Google Scholar search query from title and authors."""
    if not title:
        return None
    # For short titles, add author names to disambiguate
    query = title
    if authors and len(title) < 60:
        # Add first 2 author surnames
        author_str = " ".join(authors[:2])
        query = f"{title} {author_str}"
    return query


def download_paper(doi, title=None, cache_dir=DEFAULT_CACHE_DIR,
                   email=None, force=False, use_scihub=False):
    """Main download logic. Returns (path, candidate_urls) tuple.

    path is the file path on success, None on failure.
    candidate_urls is a list of URLs found during the process (for manual fallback).
    """
    doi = clean_doi(doi)
    cache_path = doi_to_cache_path(doi, cache_dir)
    email = email or os.environ.get("RESEARCHER_EMAIL", DEFAULT_EMAIL)
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")
    candidate_urls = []  # Collect URLs for manual download fallback

    # Tier 1: Cache
    if not force and validate_pdf(cache_path):
        log("Found in cache")
        return cache_path, []

    os.makedirs(cache_dir, exist_ok=True)

    # Always add the DOI URL as a candidate for manual download
    candidate_urls.append(f"https://doi.org/{doi}")

    # Tier 1b: OSF preprints
    osf_url = get_osf_pdf_url(doi)
    if osf_url:
        log("Trying OSF direct download...")
        if download_pdf(osf_url, cache_path):
            log("Downloaded from OSF")
            return cache_path, candidate_urls

    # Tier 2: Unpaywall
    log("Querying Unpaywall...")
    unpaywall = get_unpaywall_info(doi, email)

    if unpaywall.get("pdf_url"):
        candidate_urls.append(unpaywall["pdf_url"])
        log(f"  Found direct PDF URL: {unpaywall['pdf_url']}")
        if download_pdf(unpaywall["pdf_url"], cache_path):
            log("Downloaded via Unpaywall (direct PDF)")
            return cache_path, candidate_urls

    # Tier 3: Landing page scrape
    for landing_url in unpaywall.get("landing_urls", []):
        candidate_urls.append(landing_url)
        log(f"  Scraping landing page: {landing_url}")
        time.sleep(0.5)
        pdf_url = extract_pdf_from_landing_page(landing_url)
        if pdf_url:
            candidate_urls.append(pdf_url)
            log(f"  Found PDF link: {pdf_url}")
            if download_pdf(pdf_url, cache_path):
                log("Downloaded via landing page scrape")
                return cache_path, candidate_urls

    # Tier 4: Google Scholar via SerpAPI
    crossref = get_metadata_from_crossref(doi) if not title else {}
    search_title = title or unpaywall.get("title") or crossref.get("title")
    authors = crossref.get("authors", [])
    scholar_query = build_scholar_query(search_title, authors)
    scholar_urls = []
    if scholar_query:
        log(f"Searching Google Scholar for: {scholar_query[:80]}...")
        time.sleep(0.5)
        scholar_urls = search_google_scholar(scholar_query, serpapi_key) or []
        candidate_urls.extend(scholar_urls)
        for scholar_url in scholar_urls:
            if download_pdf(scholar_url, cache_path):
                log("Downloaded via Google Scholar")
                return cache_path, candidate_urls
    else:
        log("No title available for Google Scholar search")

    # Tier 4b: Browser-based download for Google Scholar URLs that failed
    # (e.g. Academia.edu login wall)
    for scholar_url in scholar_urls:
        log(f"  Trying browser download: {scholar_url}")
        if download_pdf_via_browser(scholar_url, cache_path):
            log("Downloaded via browser automation (Google Scholar link)")
            return cache_path, candidate_urls

    # Tier 4c: Browser automation for landing pages that failed HTML scrape
    for landing_url in unpaywall.get("landing_urls", []):
        log(f"  Trying browser automation for landing page: {landing_url}")
        if download_pdf_via_browser(landing_url, cache_path):
            log("Downloaded via browser automation (landing page)")
            return cache_path, candidate_urls

    # Tier 5: Sci-Hub (only when explicitly enabled — see the note above)
    if use_scihub:
        log("Trying Sci-Hub...")
        time.sleep(1)
        scihub_url, referer = get_scihub_pdf_url(doi)
        if scihub_url:
            candidate_urls.append(scihub_url)
            if download_pdf(scihub_url, cache_path, referer=referer):
                log("Downloaded via Sci-Hub")
                return cache_path, candidate_urls

    return None, candidate_urls


# =============================================================================
# CLI
# =============================================================================

def pick_best_url_for_manual(candidate_urls):
    """Pick the best URL for manual download (prefer DOI, then direct PDFs)."""
    if not candidate_urls:
        return None
    # Prefer doi.org link (works with institutional access)
    for u in candidate_urls:
        if "doi.org/" in u:
            return u
    # Then prefer direct PDF links
    for u in candidate_urls:
        if u.endswith(".pdf") or "/pdf/" in u:
            return u
    return candidate_urls[0]


def main():
    parser = argparse.ArgumentParser(
        description="Download academic PDF by DOI with multi-source fallback."
    )
    parser.add_argument("--doi", required=True, help="DOI of the paper")
    parser.add_argument("--title", help="Paper title (for Google Scholar fallback)")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help=f"Cache directory (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument("--email", help="Email for Unpaywall API")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    parser.add_argument("--scihub", action="store_true",
                        help="Also try Sci-Hub (off by default; enable at your own risk)")
    parser.add_argument("--open", action="store_true",
                        help="On failure, ask user whether to open best URL in browser")

    args = parser.parse_args()

    path, candidate_urls = download_paper(
        doi=args.doi,
        title=args.title,
        cache_dir=args.cache_dir,
        email=args.email,
        force=args.force,
        use_scihub=args.scihub,
    )

    if path:
        print(os.path.abspath(path))
        sys.exit(0)

    log("All automatic download sources exhausted.")

    if candidate_urls:
        log(f"Candidate URLs found: {len(candidate_urls)}")
        for u in candidate_urls:
            log(f"  - {u}")

    best_url = pick_best_url_for_manual(candidate_urls)

    if args.open and best_url:
        log(f"\nOpening in browser: {best_url}")
        import subprocess
        import platform
        if platform.system() == "Darwin":
            subprocess.run(["open", best_url])
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", best_url])
        else:
            subprocess.run(["start", best_url], shell=True)
        expected_cache = doi_to_cache_path(args.doi, args.cache_dir)
        log(f"Save the PDF to: {os.path.abspath(expected_cache)}")
        # Exit with code 2 to signal "opened in browser, waiting for manual download"
        sys.exit(2)

    sys.exit(1)


if __name__ == "__main__":
    main()
