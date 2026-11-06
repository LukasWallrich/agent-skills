#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fetchpdf @ git+https://github.com/The-Metascience-Observatory/fetchpdf@82a4c474178a3e4245da82542c3a25434674e48e",
#   "requests",
#   "ddgs",
#   "boto3",
# ]
# ///
"""Download an academic PDF by DOI, and check that it is that DOI's paper.

Before downloading, it looks in the local cache, the user's Zotero libraries (local
API) and the shared R2 cache. Every PDF it verifies goes to the local and R2 caches.
Only --add-to-zotero adds to the library.

The download steps:
  A. `fetchpdf.fetch_pdf` — the open-access chain (OpenAlex, Unpaywall, PMC,
     Crossref links, preprint servers, repository landing pages, publisher
     routes). Every PDF it writes is checked against the requested record.
  P. Publisher APIs, each only with its key and only for that publisher's DOIs:
     Wiley's text-and-data-mining API (a PDF, under the same identity check) and
     the Springer Nature Open Access API (JATS XML, never a PDF).
  W. A web search (the `ddgs` package, DuckDuckGo and other engines) for
     "<DOI> pdf", then '"<title>" pdf', when step A found nothing. It finds
     author homepages, course pages and untagged repositories. PDF links whose
     result title matches the paper are downloaded and kept only if the identity
     check passes. A blocked or failed search just means this step found nothing.
  B. Google Scholar via SerpAPI, when steps A and W found nothing and
     a SerpAPI key is available. Each PDF link is downloaded and accepted only if
     fetchpdf's identity check says it is the requested paper.
  C. The user's running, signed-in Chrome (macOS), when the steps above found
     nothing. One tab of a minimised worker window walks the Scholar links plain
     HTTP could not fetch, then the publisher's own PDF URLs, then EBSCOhost.
     `--no-browser` skips it.

On success: prints the absolute path to the PDF on stdout, exits 0.
On failure: exits 1 (exit 2 with --open, after opening a candidate URL).
stdout only ever carries a verified PDF's path. Springer's XML is reported on
stderr as a `FULLTEXT_XML=<path>` line; with --accept-xml it also ends the walk
and the run exits 3.
All diagnostic output goes to stderr.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import ssl
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import institutional_fetch as chrome
from r2_cache import R2Cache
from zotero_local import NEEDS_PDF_TAG, TAG as ZOTERO_TAG, ZoteroError, ZoteroLocal

DEFAULT_CACHE_DIR = os.path.expanduser("~/.claude/cache/pdfs")
DEFAULT_EMAIL = "unpaywall@impactstory.org"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SCHOLAR_RESULTS = 5
WEB_RESULTS = 30            # results per web search
WEB_MAX_PDFS = 5            # title-matched PDF links downloaded per query
WEB_SEARCH_SPACING = 1.5    # seconds between web searches, across runs
WEB_CACHE_DAYS = 7
KEY_FILE = os.path.expanduser("~/.claude/api_keys.env")
#: fetchpdf's names for keys the key file holds under other names.
FETCHPDF_KEY_NAMES = {"COREAPIKEY": "CORE_API_KEY",
                      "ELSEVIER_TDM_API_KEY": "ELSEVIER_API_KEY",
                      "OPENALEXAPIKEY": "OPENALEX_API_KEY"}
#: Registrants whose articles Wiley Online Library hosts, including the BPS (10.1348)
#: and Physiological Society (10.1113) back files.
WILEY_PREFIXES = frozenset({"10.1002", "10.1111", "10.1046", "10.1034", "10.1348", "10.1113"})
WILEY_TDM_URL = "https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"
WILEY_TDM_SPACING = 10.0    # documented limit: 60 requests per 10 minutes, across runs
SPRINGER_NATURE_PREFIXES = frozenset({"10.1007", "10.1038", "10.1057", "10.1186", "10.3758"})
SPRINGER_OA_URL = "https://api.springernature.com/openaccess/jats"
SPRINGER_OA_SPACING = 1.0   # basic plan: 100 requests a minute, 500 a day
#: Below this much body text, a JATS record is a stub rather than the paper.
JATS_MIN_BODY_CHARS = 1000
#: An API that refused the key or the quota refuses every later call too, so these
#: stay off for the rest of the process. A new process asks again.
_wiley_off = False
_springer_off = False

#: stdout is reserved for the result path. fetchpdf prints its progress there,
#: so every call into it runs with stdout redirected to this stream.
STDOUT = sys.stdout


def log(msg):
    print(msg, file=sys.stderr)


def redact(text, *secrets):
    """`text` with each secret replaced. Requests' errors quote the whole URL."""
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def wait_for_turn(stamp, spacing):
    """Space calls to one service at least `spacing` seconds apart, across runs."""
    if os.path.exists(stamp):
        time.sleep(max(0.0, spacing - (time.time() - os.path.getmtime(stamp))))
    with open(stamp, "w"):
        pass


def clean_doi(doi):
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
    doi = re.sub(r"^doi:", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def doi_to_cache_path(doi, cache_dir):
    """DOIs are case-insensitive, so the key is too. `clean_doi` keeps the case for URLs."""
    md5 = hashlib.md5(clean_doi(doi).lower().encode()).hexdigest()
    return os.path.join(cache_dir, f"{md5}.pdf")


def legacy_cache_path(doi, cache_dir):
    """Where entries keyed before the key was lowercased live."""
    md5 = hashlib.md5(clean_doi(doi).encode()).hexdigest()
    return os.path.join(cache_dir, f"{md5}.pdf")


def doi_to_xml_path(doi, cache_dir):
    return doi_to_cache_path(doi, cache_dir)[:-len(".pdf")] + ".jats.xml"


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
    # Crossref titles can carry JATS markup ("<i>Drosophila</i>"), which a PDF never prints.
    title, subtitle = ((re.sub(r"<[^>]+>", "", t) if t else t)
                       for t in ((msg.get(k) or [None])[0] for k in ("title", "subtitle")))
    return {
        "title": title,
        "search_title": f"{title}: {subtitle}" if title and subtitle else title,
        "authors": [a["family"] for a in msg.get("author", []) if a.get("family")],
        "pages": msg.get("page") or "",
    }


#: A DOI-only acceptance also needs this much of the title on the front two
#: pages (fetchpdf's gap-tolerant `matched_fraction`). Measured on 121 cached
#: PDFs whose DOI fetchpdf found in the text: a paper citing the DOI scored 0.52,
#: a supplementary-information file 0.64, a line-numbered medRxiv preprint (the
#: right paper) 0.76, and every other right paper 0.97 or more.
DOI_ONLY_TITLE_MIN = 0.70

#: Below this much squashed front-page text, a missing title says nothing.
LEGIBLE_FRONT_CHARS = 400


def verify_identity(path, doi, title, pages):
    """fetchpdf's verdict on whether the PDF at `path` is this DOI's paper.

    fetchpdf accepts a PDF whose first three pages print the requested DOI,
    which a paper *citing* it, or its supplementary file, does too: for
    10.1037/0022-3514.74.6.1464 it accepted a 2021 article quoting that DOI in
    its abstract. So a DOI-only acceptance is refused when the front pages are
    legible and carry too little of the title.
    """
    with contextlib.redirect_stdout(sys.stderr):
        from fetchpdf.retrieval._util import matched_fraction, squash
        from fetchpdf.retrieval.pdf_identity import WRONG, IdentityVerdict, verify_pdf_identity
        from fetchpdf.retrieval.pdf_text import pdf_text
        from fetchpdf.retrieval.resolve import arxiv_id_from_doi

        verdict = verify_pdf_identity(
            path, doi, title, pages=pages, arxiv_id=arxiv_id_from_doi(doi)
        )
        if not (verdict.ok and "doi-in-text" in verdict.signals and title):
            return verdict
        front = squash(pdf_text(path, max_pages=2, min_chars=1) or "")
        fraction = matched_fraction(squash(title), front)
    if len(front) >= LEGIBLE_FRONT_CHARS and fraction < DOI_ONLY_TITLE_MIN:
        return IdentityVerdict(
            WRONG, f"the requested DOI is printed, but only {fraction:.0%} of the title "
            f'"{title[:70]}" is on the front pages -- likely a paper citing it, '
            "or a supplement")
    return verdict


# =============================================================================
# Step A: the fetchpdf chain
# =============================================================================

def fetchpdf_download(doi, tmp_path, email, use_playwright, title=None, pages=None):
    """Run the fetchpdf chain into tmp_path. Returns True when it wrote a PDF
    that also passes `verify_identity`; a refused PDF is deleted."""
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
    if written is None or not validate_pdf(tmp_path):
        return False
    verdict = verify_identity(tmp_path, doi, title, pages)
    if verdict.ok:
        return True
    log(f"  fetchpdf's PDF rejected ({verdict.state}): {verdict.reason}")
    os.unlink(tmp_path)
    return False


# =============================================================================
# Step P: publisher APIs, each conditional on its key
# =============================================================================

def wiley_tdm_pdf(doi, token, cache_dir):
    """PDF bytes from Wiley's text-and-data-mining API, or None.

    The API redirects to a separate download host, and requests and urllib both
    forward a custom header across hosts, so the redirect is followed by hand
    without the token. Entitlement is read off the API's own answer: its 401/403
    means this token and network may not have the article, while a refusal from
    the download host says nothing about that.
    """
    global _wiley_off
    url = WILEY_TDM_URL.format(doi=quote(doi, safe=""))
    agent = {"User-Agent": "download-paper"}
    wait_for_turn(os.path.join(cache_dir, ".last-wiley-tdm"), WILEY_TDM_SPACING)
    try:
        r = requests.get(url, headers={**agent, "Wiley-TDM-Client-Token": token},
                         timeout=60, allow_redirects=False)
        if r.status_code in (401, 403):
            log(f"  Wiley TDM: HTTP {r.status_code}, not entitled to this article")
            return None
        if r.status_code == 429:
            _wiley_off = True
            log("  Wiley TDM: HTTP 429, rate limited; route off for this run")
            return None
        if r.is_redirect:
            r = requests.get(urljoin(url, r.headers["Location"]), headers=agent, timeout=60)
    except requests.RequestException as e:
        log(f"  Wiley TDM: request failed: {redact(e, token)}")
        return None
    if r.status_code != 200 or not r.content.startswith(b"%PDF-"):
        log(f"  Wiley TDM: no PDF (HTTP {r.status_code})")
        return None
    return r.content


def jats_article(body, doi, title):
    """The <article> in a Springer response that is this DOI's full text, or None.

    `verify_identity` reads PDFs only, so this applies its tests to the XML: the
    record must carry the requested DOI, enough of the title (the same threshold)
    and a real body, because JATS front matter alone holds only the abstract.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        log("  Springer OA: the answer is not XML")
        return None
    for article in root.iter("article"):
        ids = [(e.text or "").strip().lower() for e in article.findall("front/article-meta/article-id")
               if e.get("pub-id-type") == "doi"]
        if doi.lower() not in ids:
            continue
        body_el = article.find("body")
        if body_el is None or len("".join(body_el.itertext()).strip()) < JATS_MIN_BODY_CHARS:
            log("  Springer OA: the record has no full-text body")
            return None
        found = article.find("front/article-meta/title-group/article-title")
        if found is None or not "".join(found.itertext()).strip():
            log("  Springer OA: the record has no article title")
            return None
        if title:
            with contextlib.redirect_stdout(sys.stderr):
                from fetchpdf.retrieval._util import matched_fraction, squash

                fraction = matched_fraction(squash(title), squash("".join(found.itertext())))
            if fraction < DOI_ONLY_TITLE_MIN:
                log(f"  Springer OA: rejected, only {fraction:.0%} of the title matches")
                return None
        return article
    log("  Springer OA: no record for this DOI in the answer")
    return None


def springer_oa_xml(doi, title, api_key, cache_dir, force=False):
    """Path of this DOI's verified JATS XML from the Springer Nature OA API, or None.

    The key rides in the query string, so the URL is never logged, exception text
    is redacted, and redirects are not followed. A 404 is the API's normal answer
    for anything not open access.
    """
    global _springer_off
    path = doi_to_xml_path(doi, cache_dir)
    if not force and os.path.exists(path):
        return path
    wait_for_turn(os.path.join(cache_dir, ".last-springer-oa"), SPRINGER_OA_SPACING)
    try:
        r = requests.get(SPRINGER_OA_URL, params={"q": f"doi:{doi}", "api_key": api_key},
                         headers={"User-Agent": "download-paper"},
                         timeout=60, allow_redirects=False)
    except requests.RequestException as e:
        log(f"  Springer OA: request failed: {redact(e, api_key)}")
        return None
    if r.status_code in (401, 403, 429):
        _springer_off = True
        log(f"  Springer OA: HTTP {r.status_code}, refused (key or daily quota); "
            "route off for this run")
        return None
    if r.status_code == 404:
        log("  Springer OA: no open-access record for this DOI (HTTP 404)")
        return None
    if r.status_code != 200:
        log(f"  Springer OA: HTTP {r.status_code}")
        return None
    article = jats_article(r.content, doi, title)
    if article is None:
        return None
    for prefix, uri in (("xlink", "http://www.w3.org/1999/xlink"),
                        ("mml", "http://www.w3.org/1998/Math/MathML")):
        ET.register_namespace(prefix, uri)
    # Written whole under the final name, so a stopped run never leaves half a file.
    with tempfile.NamedTemporaryFile(dir=cache_dir, prefix="part-", suffix=".xml",
                                     delete=False) as f:
        f.write(ET.tostring(article, encoding="utf-8"))
    os.replace(f.name, path)
    return path


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


def walk_pdf_links(links, tmp_path, doi, title, pages, label="Scholar"):
    """Walk PDF links, keeping the first that is the requested paper.

    Returns (accepted_url, blocked_urls, wrong_urls). `blocked` are links that
    did not yield PDF bytes (403, bot-check HTML, a landing page) — candidates
    for the signed-in Chrome route. `wrong` are PDFs of some other document.
    """
    blocked, wrong = [], []
    for url in links:
        log(f"  {label} candidate: {url}")
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
# Step W: web search (ddgs)
# =============================================================================

def normalise_title(text):
    """Lowercase words only, minus the "PDF" tags and "..." truncation search
    engines put on result titles."""
    text = re.sub(r"^\W*pdf\b\W*", "", (text or "").lower())
    return " ".join(re.findall(r"[^\W_]+", text))


def web_title_match(result_title, paper_title):
    """Whether a search result's (often truncated) title is this paper's title.

    A truncated result title must be a substring of the paper title; a result
    title with extra words (a site name) must contain it. Fragments under 20
    characters only count as an exact match, so "Introduction" matches nothing.
    """
    found, wanted = normalise_title(result_title), normalise_title(paper_title)
    if not found or not wanted:
        return False
    if found == wanted:
        return True
    return (len(found) >= 20 and found in wanted) or (len(wanted) >= 20 and wanted in found)


def web_search(query, cache_dir):
    """Search results for `query` as a list of {title, href} dicts, or [].

    Non-empty results are cached for WEB_CACHE_DAYS. Searches are spaced at
    least WEB_SEARCH_SPACING apart, across runs, through a timestamp file.
    `ddgs` scrapes search engines, so any failure — rate limit, changed page,
    missing package — counts as "found nothing".
    """
    search_dir = os.path.join(cache_dir, "web-search")
    os.makedirs(search_dir, exist_ok=True)
    cache_file = os.path.join(search_dir, hashlib.md5(query.encode()).hexdigest() + ".json")
    if os.path.exists(cache_file) and \
            time.time() - os.path.getmtime(cache_file) < WEB_CACHE_DAYS * 86400:
        with open(cache_file) as f:
            return json.load(f)

    wait_for_turn(os.path.join(search_dir, ".last-search"), WEB_SEARCH_SPACING)
    try:
        from ddgs import DDGS

        results = DDGS(timeout=15).text(query, max_results=WEB_RESULTS) or []
    except Exception as e:
        log(f"  Web search failed ({type(e).__name__}: {e}), treating as no results")
        results = []

    results = [{"title": r.get("title", ""), "href": r.get("href", "")} for r in results]
    if results:
        with open(cache_file, "w") as f:
            json.dump(results, f)
    return results


def web_pdf_links(results, paper_title):
    """PDF links among the results whose title matches the paper, in result order."""
    links = [r["href"] for r in results
             if ".pdf" in r["href"].lower() and web_title_match(r["title"], paper_title)]
    return list(dict.fromkeys(links))[:WEB_MAX_PDFS]


def web_download(doi, title, tmp_path, pages, cache_dir):
    """Search for "<DOI> pdf", then '"<title>" pdf', and walk the matched PDF links.

    Returns (accepted_url, blocked_urls). Every PDF must pass the identity
    check, so a preprint, slides or another paper with the same title is
    skipped rather than kept.
    """
    tried, blocked = set(), []
    for query in (f"{doi} pdf", f'"{title}" pdf'):
        log(f"  Web search: {query[:90]}")
        links = [u for u in web_pdf_links(web_search(query, cache_dir), title) if u not in tried]
        if not links:
            log("    no title-matched PDF links")
            continue
        tried.update(links)
        accepted, more_blocked, _ = walk_pdf_links(links, tmp_path, doi, title, pages,
                                                   label="Web search")
        blocked += more_blocked
        if accepted:
            return accepted, blocked
    return None, blocked


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
    """Walk the browser candidates in the worker window's tab. Returns the URL kept."""
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

_shared = {}
FIND_PDF_HINTS = {
    "not-installed": "the Find PDF API plugin is not in Zotero; see zotero-plugin/README.md",
    "zotero-closed": "open Zotero and run this again",
    "local-api-off": "switch on Settings > Advanced > Allow other applications on this computer "
                     "to communicate with Zotero",
    "unsupported": "this Zotero version lacks what the plugin relies on",
    "not-eligible": "the item already has a file, or has no DOI, URL or PMCID",
}


def r2_cache():
    """The shared bucket, or None when this machine has no settings for it."""
    if "r2" not in _shared:
        _shared["r2"] = R2Cache.from_keys(load_key_file())
    return _shared["r2"]


def zotero():
    """The local Zotero API, or None when it cannot be reached."""
    if "zotero" not in _shared:
        _shared["zotero"] = ZoteroLocal.connect()
    return _shared["zotero"]


def share_pdf(path, doi, route, overwrite=False):
    """Upload a verified PDF to the shared cache. A failure only leaves it local."""
    r2 = r2_cache()
    if r2 and r2.store(path, os.path.basename(doi_to_cache_path(doi, "")), overwrite=overwrite,
                       doi=clean_doi(doi).lower(), route=route, fetched=time.strftime("%Y-%m-%d")):
        log(f"Stored in the shared cache ({route})")


def find_existing(doi, title, pages, tmp_path):
    """Fill tmp_path from a copy we already hold. Returns its source, or None.

    A library PDF or bucket object still has to pass the identity check: neither
    is known to carry this DOI's paper until it has.
    """
    zot = zotero()
    if zot:
        try:
            for library, path in zot.library_pdfs(doi):
                with open(path, "rb") as f:
                    if accept_bytes(f.read(), tmp_path, doi, title, pages, f"Zotero {library}"):
                        return "zotero"
        except (OSError, requests.RequestException, ValueError) as error:
            log(f"Zotero library lookup failed: {error}")
    r2 = r2_cache()
    if r2:
        names = {os.path.basename(p(doi, "")) for p in (doi_to_cache_path, legacy_cache_path)}
        for name in sorted(names):
            source = r2.fetch(name, tmp_path)
            if source is None:
                continue
            verdict = verify_identity(tmp_path, doi, title, pages)
            if verdict.ok:
                log(f"Found in the shared cache (first fetched via {source.get('route', 'unknown')})")
                return "r2"
            log(f"Shared cache: rejected ({verdict.state}) {verdict.reason}")
            os.unlink(tmp_path)
    return None


def doi_csl(doi):
    """doi.org's CSL-JSON, which covers Crossref and DataCite DOIs alike."""
    body = fetch_url(f"https://doi.org/{quote(clean_doi(doi), safe='/')}", timeout=20,
                     accept="application/vnd.citationstyles.csl+json")
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def report_library_status(doi):
    """State on stderr whether the personal library holds this DOI."""
    zot = zotero()
    if not zot:
        log("ZOTERO=unavailable")
        return
    try:
        key, has_pdf = zot.status(doi)
    except (requests.RequestException, ValueError) as error:
        log(f"Zotero library check failed: {error}")
        log("ZOTERO=unavailable")
        return
    log(f"ZOTERO=in-library key={key} pdf={'yes' if has_pdf else 'no'}" if key else "ZOTERO=not-in-library")


def add_to_zotero(doi, pdf_path):
    """Add the paper to the personal library. A failure leaves the download's result alone."""
    zot = zotero()
    if not zot:
        log(f"ZOTERO=not-added (Zotero not reachable). With Zotero open, run: "
            f"{shlex.quote(os.path.abspath(__file__))} --doi {shlex.quote(clean_doi(doi))} --add-to-zotero")
        return
    try:
        action, key = zot.add_paper(doi, pdf_path, doi_csl)
    except (ZoteroError, requests.RequestException, OSError, ValueError) as error:
        log(f"ZOTERO=not-added ({error})")
        return
    log(f"ZOTERO={action} key={key} pdf={'yes' if pdf_path or action == 'already' else 'no'}")
    return key


def zotero_find_pdf(key, doi, title=None, cache_dir=DEFAULT_CACHE_DIR):
    """Ask Zotero to find a PDF for this item. Returns the cached path of a verified one, or None.

    A file from Zotero is unverified until it passes the identity check. One that fails is
    taken back out of the library and never reaches the local or shared cache.
    """
    zot = zotero()
    if not zot:
        log("ZOTERO_FIND_PDF=zotero-closed")
        return None
    if not key:
        log("ZOTERO_FIND_PDF=skipped (the item was not added)")
        return None
    log("Asking Zotero's Find Available PDF, which can take a few minutes...")
    result = zot.find_available_pdf(key)
    state = result["state"]
    if state != "found":
        detail = FIND_PDF_HINTS.get(state) or result.get("message")
        log(f"ZOTERO_FIND_PDF={state}" + (f" ({detail})" if detail else ""))
        return None

    attachment = result.get("attachmentKey")
    via = (result.get("methodsTried") or ["unknown"])[-1]
    doi = clean_doi(doi)
    cache_path = doi_to_cache_path(doi, cache_dir)
    tmp_path = None
    rejected = None
    try:
        path = zot.file_path(attachment)
        if result.get("contentType") != "application/pdf":
            rejected = f"Zotero found a {result.get('contentType')} file, not a PDF"
        elif not path:
            rejected = "the attachment's file is not on this machine"
        else:
            meta = crossref_metadata(doi)
            os.makedirs(cache_dir, exist_ok=True)
            handle, tmp_path = tempfile.mkstemp(dir=cache_dir, prefix="part-", suffix=".pdf")
            os.close(handle)
            with open(path, "rb") as f:
                ok = accept_bytes(f.read(), tmp_path, doi, meta.get("title") or title,
                                  meta.get("pages"), "Zotero Find Available PDF")
            if not (ok and validate_pdf(tmp_path)):
                rejected = "it did not pass the identity check"
        if not rejected:
            os.replace(tmp_path, cache_path)
            share_pdf(cache_path, doi, "zotero-find-pdf")
            zot.remove_tag(key, NEEDS_PDF_TAG)
            log(f"ZOTERO_FIND_PDF=found key={key} attachment={attachment} via={via}")
            return cache_path
    except Exception as error:  # an unchecked file must not stay, whatever went wrong
        rejected = f"the check failed: {error}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    removed = zot.delete_item(attachment)
    if removed and result.get("itemTagged"):
        zot.remove_tag(key, ZOTERO_TAG)
    log(f"ZOTERO_FIND_PDF=rejected key={key} attachment={attachment} via={via} "
        f"removed={'yes' if removed else 'no'} ({rejected})")
    return None


def download_paper(doi, title=None, cache_dir=DEFAULT_CACHE_DIR, email=None,
                   force=False, use_playwright=False, use_browser=True,
                   ebsco_profile="", accept_xml=False):
    """Returns (path, candidates). path is None on failure.

    `candidates` is an ordered list of (url, note) for manual download. With
    `accept_xml`, path can be a `.jats.xml` file: the walk stops at Springer's XML
    instead of carrying on to look for a PDF.
    """
    doi = clean_doi(doi)
    cache_path = doi_to_cache_path(doi, cache_dir)
    legacy_path = legacy_cache_path(doi, cache_dir)
    if legacy_path != cache_path and not os.path.exists(cache_path) and os.path.exists(legacy_path):
        os.replace(legacy_path, cache_path)
    if not force and validate_pdf(cache_path):
        log("Found in cache")
        return cache_path, []
    xml_path = doi_to_xml_path(doi, cache_dir)
    if accept_xml and not force and os.path.exists(xml_path):
        log("Found the XML in cache")
        log(f"FULLTEXT_XML={xml_path}")
        return xml_path, []

    os.makedirs(cache_dir, exist_ok=True)
    r2 = r2_cache()
    meta = crossref_metadata(doi)
    query_title = title or meta.get("search_title")
    candidates = []

    # A downloaded PDF is moved onto the cache path only once it is a valid
    # PDF, so an interrupted or refused download never stands in for the paper.
    handle, tmp_path = tempfile.mkstemp(dir=cache_dir, prefix="part-", suffix=".pdf")
    os.close(handle)
    os.unlink(tmp_path)

    def keep(route):
        os.replace(tmp_path, cache_path)
        share_pdf(cache_path, doi, route, overwrite=force)

    blocked = []
    try:
        if not force:
            found = find_existing(doi, meta.get("title") or title, meta.get("pages"), tmp_path)
            if found:
                os.replace(tmp_path, cache_path)
                if found == "zotero":
                    share_pdf(cache_path, doi, "zotero")
                return cache_path, candidates
            if accept_xml and r2 and r2.fetch(os.path.basename(xml_path), tmp_path) is not None:
                with open(tmp_path, "rb") as f:
                    article = jats_article(f.read(), doi, meta.get("title") or title)
                if article is not None:
                    os.replace(tmp_path, xml_path)
                    log("Found verified XML in the shared cache")
                    log(f"FULLTEXT_XML={xml_path}")
                    return xml_path, []
                os.unlink(tmp_path)

        log(f"Fetching {doi} via fetchpdf...")
        if fetchpdf_download(doi, tmp_path, email, use_playwright,
                             meta.get("title") or title, meta.get("pages")):
            keep("fetchpdf")
            log("Downloaded via fetchpdf")
            return cache_path, candidates

        keys = load_key_file()
        prefix = doi.split("/", 1)[0]
        if keys.get("WILEY_TDM_TOKEN") and prefix in WILEY_PREFIXES and not _wiley_off:
            log("Trying Wiley's text-and-data-mining API...")
            if accept_bytes(wiley_tdm_pdf(doi, keys["WILEY_TDM_TOKEN"], cache_dir), tmp_path,
                            doi, meta.get("title") or title, meta.get("pages"), "Wiley TDM"):
                keep("wiley-tdm")
                log("Downloaded via Wiley's TDM API")
                return cache_path, candidates
        if (keys.get("SPRINGER_OA_API_KEY") and prefix in SPRINGER_NATURE_PREFIXES
                and not _springer_off):
            log("Trying the Springer Nature Open Access API...")
            found = springer_oa_xml(doi, meta.get("title") or title,
                                    keys["SPRINGER_OA_API_KEY"], cache_dir, force)
            if found:
                log(f"FULLTEXT_XML={found}")
                if r2:
                    r2.store(found, os.path.basename(found), overwrite=force,
                             doi=clean_doi(doi).lower(), route="springer-oa")
                if accept_xml:
                    return found, candidates

        if meta.get("title") or title:
            log("Searching the web for a PDF...")
            accepted, web_blocked = web_download(
                doi, meta.get("title") or title, tmp_path, meta.get("pages"), cache_dir)
            if accepted:
                keep("web-search")
                log(f"Downloaded via web search: {accepted}")
                return cache_path, candidates
            candidates += [(u, "web-search link, no PDF by plain HTTP") for u in web_blocked]
        else:
            log("No title available, skipping the web-search step")

        api_keys = serpapi_keys(keys)
        query = build_scholar_query(query_title, meta.get("authors"))
        if api_keys and query:
            log(f"Searching Google Scholar for: {query[:80]}")
            links = scholar_pdf_links(query, api_keys)
            accepted, blocked, wrong = walk_pdf_links(
                links, tmp_path, doi, meta.get("title") or title, meta.get("pages"))
            if accepted:
                keep("google-scholar")
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
                keep("chrome")
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
    parser.add_argument("--accept-xml", action="store_true",
                        help="Stop at Springer's JATS XML rather than look further for a "
                             "PDF: exit 3, with the path on stderr as FULLTEXT_XML=<path>")
    parser.add_argument("--add-to-zotero", action="store_true",
                        help="Add the paper to the personal Zotero library: the Agent inbox "
                             "collection, or the existing item if it has no PDF. For a paper the "
                             "user asked for, never for a batch")
    parser.add_argument("--zotero-find-pdf", action="store_true",
                        help="With --add-to-zotero, when no PDF was found, ask Zotero's Find "
                             "Available PDF, using the resolvers set up in Zotero. Needs the "
                             "plugin in zotero-plugin/; without it, only logs a line")
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

    report_library_status(args.doi)
    path, candidates = download_paper(
        doi=args.doi,
        title=args.title,
        cache_dir=args.cache_dir,
        email=email,
        force=args.force,
        use_playwright=args.playwright,
        use_browser=not args.no_browser,
        ebsco_profile=keys.get("INSTITUTION_EBSCO_PROFILE", ""),
        accept_xml=args.accept_xml,
    )

    if args.add_to_zotero:
        has_pdf = bool(path and path.endswith(".pdf"))
        key = add_to_zotero(args.doi, path if has_pdf else None)
        if args.zotero_find_pdf and not has_pdf:
            found = zotero_find_pdf(key, args.doi, args.title, args.cache_dir)
            if found:
                path, candidates = found, []
    elif args.zotero_find_pdf:
        log("ZOTERO_FIND_PDF=skipped (it needs --add-to-zotero)")

    if path and path.endswith(".xml"):
        sys.exit(3)
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
