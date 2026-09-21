#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fetchpdf @ git+https://github.com/The-Metascience-Observatory/fetchpdf@82a4c474178a3e4245da82542c3a25434674e48e",
#   "requests",
# ]
# ///
"""Fetch a paywalled PDF through the user's logged-in, visible Chrome (macOS).

The Chrome layer here is shared: `download_paper.py` imports it for its browser
step, and this file's CLI uses it directly. Everything addresses ONE tab by its
AppleScript `id`, so a run never touches the tab the user is working in.

Routes:
  1. Navigate-then-fetch — point the tab at a PDF URL, wait until Chrome renders
     it as `application/pdf`, then read the bytes with an in-page credentialed
     `fetch`. The asset host is same-origin once the tab is on it, so CORS
     allows the read, and bot-check interstitials (Cloudflare, Anubis, Imperva)
     clear themselves while the poll runs.
  2. EBSCOhost (--ebsco) — best for psychology (APA PsycInfo, and Springer /
     Cloudflare-blocked papers EBSCO indexes). Search BY DOI, read the record id,
     open the PDF viewer, take the signed content.ebscohost.com URL out of the
     viewer's resource timings, and curl it. That signed URL is
     SELF-AUTHENTICATING — no cookies, no CORS dance.
  3. Direct publisher PDF — the publisher's canonical PDF URL fetched with a
     domain-aware cookie jar exported from the browser.

Prereqs: log in once at the publisher / EBSCO in your real Chrome (via your
institution / OpenAthens); Chrome's *View ▸ Developer ▸ Allow JavaScript from
Apple Events* enabled once. For route 3 also run `export-cookies` afterwards.

Usage:
  ./institutional_fetch.py --doi 10.1037/edu0000827 --ebsco [--out out.pdf]
  ./institutional_fetch.py --doi 10.1080/00224545.2024.2439953
  ./institutional_fetch.py export-cookies         # after logging in

Set INSTITUTION_EBSCO_PROFILE to your library's cluster id — the <cluster> in the
research.ebsco.com/c/<cluster>/... URL you land on after logging in.

Gotchas learned the hard way:
  - Chrome's AppleScript tab `id` compares as a STRING. A numeric `=` matches
    nothing and every command silently does nothing.
  - Chrome's PDF viewer refuses to serialise a Promise back to AppleScript, so
    the in-page fetch parks its base64 on `window` and a poll collects it.
  - A URL served with `Content-Disposition: attachment` never reaches the
    viewer — Chrome drops the file in ~/Downloads and the tab does not move.
    Prefer inline PDF URLs.
  - A full browser cookie set is thousands of cookies; sending all of them to one
    host returns "400 Request Header Or Cookie Too Large". Build a per-domain jar
    so requests sends only the matching host's cookies.
  - Headless browsers are flagged by Cloudflare at most publishers, which is why
    everything here goes through the real, visible browser.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote

import requests

BU = ["browser-use", "--browser", "real"]
CACHE = Path.home() / ".claude" / "cache" / "pdfs"
COOKIES = Path.home() / ".claude" / "cache" / "browser_cookies.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
#: Base64 characters read back per osascript call.
CHUNK = 800_000


class ChromeError(RuntimeError):
    """Chrome refused a command — no window, JS disabled, tab gone."""


def pdf_urls(doi):
    """Constructable publisher PDF URLs by DOI prefix, inline versions first."""
    p = (doi or "").split("/")[0]
    t = {
        "10.1007": [f"https://link.springer.com/content/pdf/{doi}.pdf"],
        "10.1057": [f"https://link.springer.com/content/pdf/{doi}.pdf"],
        "10.1111": [f"https://onlinelibrary.wiley.com/doi/pdf/{doi}",
                    f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"],
        "10.1002": [f"https://onlinelibrary.wiley.com/doi/pdf/{doi}",
                    f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"],
        "10.1080": [f"https://www.tandfonline.com/doi/pdf/{doi}"],
        "10.1177": [f"https://journals.sagepub.com/doi/pdf/{doi}"],
        "10.1098": [f"https://royalsocietypublishing.org/doi/pdf/{doi}"],
        "10.1371": [f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"],
    }.get(p, [])
    m = re.search(r"ssrn\.(\d+)", doi or "", re.I)
    if m:
        i = m.group(1)
        t += [f"https://papers.ssrn.com/sol3/Delivery.cfm/{i}.pdf?abstractid={i}&mirid=1"]
    return t


def elsevier_pdf_urls(landing_url):
    """ScienceDirect PDF URLs derived from a `/science/article/pii/<PII>` URL."""
    m = re.search(r"/science/article/(?:abs/)?pii/([A-Za-z0-9]+)", landing_url or "")
    if not m:
        return []
    base = f"https://www.sciencedirect.com/science/article/pii/{m.group(1)}/pdfft"
    return [f"{base}?isDTMRedir=true", f"{base}?isDTMRedir=true&download=true"]


# ---------------------------------------------------------------------------
# Real-Chrome driver (macOS / osascript).
# ---------------------------------------------------------------------------
def _osa(script, timeout=90):
    r = subprocess.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=timeout)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def chrome_running():
    """True when Chrome is already running. Never launches it."""
    if sys.platform != "darwin":
        return False
    out, _ = _osa('application "Google Chrome" is running', timeout=15)
    return out == "true"


def _esc_js(js):
    return js.replace("\\", "\\\\").replace('"', '\\"')


class ChromeTab:
    """One tab of the user's Chrome, addressed by its AppleScript id.

    Opens at the end of the front window, restores whichever tab was in front,
    and closes itself on exit. Chrome is never activated, so focus stays put.
    """

    def __init__(self):
        self.id = None
        self._front = None

    def __enter__(self):
        count, err = _osa('tell application "Google Chrome" to return count of windows', 15)
        if not count.isdigit() or int(count) == 0:
            raise ChromeError(f"Chrome has no window open ({err or count})")
        self._front, _ = _osa(
            'tell application "Google Chrome" to return active tab index of front window', 15)
        out, err = _osa(
            'tell application "Google Chrome"\n'
            '  set t to make new tab at end of tabs of front window'
            ' with properties {URL:"about:blank"}\n'
            '  return id of t\n'
            'end tell', 30)
        if not out.isdigit():
            raise ChromeError(err or "could not open a tab")
        self.id = out
        if self._front.isdigit():
            _osa('tell application "Google Chrome" to set active tab index of front window '
                 f'to {self._front}', 15)
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _call(self, body, timeout=90):
        script = ('tell application "Google Chrome"\n'
                  '  repeat with w in windows\n'
                  '    repeat with tt in tabs of w\n'
                  f'      if (id of tt as string) is "{self.id}" then\n'
                  f'        {body}\n'
                  '      end if\n'
                  '    end repeat\n'
                  '  end repeat\n'
                  '  return "NOTAB"\n'
                  'end tell')
        return _osa(script, timeout)

    def js(self, code, timeout=90):
        """Result of one line of JavaScript in this tab, as a string."""
        out, err = self._call(f'return (execute tt javascript "{_esc_js(code)}")', timeout)
        if err:
            raise ChromeError(err)
        return "" if out == "NOTAB" else out

    def js_soft(self, code, timeout=90):
        """`js`, but an error page that refuses JS reads as an empty result."""
        try:
            return self.js(code, timeout)
        except ChromeError:
            return ""

    def nav(self, url):
        self._call(f'set URL of tt to "{url}"\n        return "OK"', 30)

    def close(self):
        if self.id:
            self._call('close tt\n        return "CLOSED"', 30)
            self.id = None

    def javascript_allowed(self):
        """Whether Chrome lets Apple Events run JavaScript in this tab."""
        try:
            return self.js("1+1", timeout=30) == "2"
        except ChromeError:
            return False

    # -- page state ---------------------------------------------------------
    def wait_for_pdf(self, timeout=20):
        """Poll until the tab renders a PDF. Bot checks clear themselves here."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js_soft("document.contentType") == "application/pdf":
                return True
            time.sleep(1)
        return False

    def wait_for_load(self, timeout=20, off_host=None):
        """Poll until the document is complete, optionally off a redirector host."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.js_soft("document.readyState+'|'+location.hostname")
            ready, _, host = state.partition("|")
            if ready == "complete" and host and host != off_host:
                return True
            time.sleep(1)
        return False

    def wait_for_js(self, code, timeout=20, blank=("", "NONE")):
        """Poll one expression until it returns something other than `blank`."""
        deadline = time.time() + timeout
        while True:
            got = self.js_soft(code)
            if got not in blank:
                return got
            if time.time() >= deadline:
                return None
            time.sleep(1)

    # -- bytes --------------------------------------------------------------
    def read_pdf_bytes(self, timeout=60):
        """Bytes of the PDF this tab is displaying, via an in-page fetch.

        Chrome's PDF viewer cannot hand a Promise back to AppleScript, so the
        fetch parks its base64 on `window` and this polls for it, then reads it
        back in chunks (one osascript call per chunk).
        """
        self.js_soft("window.__pdf=null;window.__pdferr=null;"
                "fetch(location.href,{credentials:'include'})"
                ".then(function(r){return r.arrayBuffer()})"
                ".then(function(a){var b=new Uint8Array(a),s='',i;"
                "for(i=0;i<b.length;i++)s+=String.fromCharCode(b[i]);window.__pdf=btoa(s)})"
                ".catch(function(e){window.__pdferr=String(e)});'started'")
        deadline = time.time() + timeout
        length = None
        while time.time() < deadline:
            state = self.js_soft("window.__pdferr?'ERR':(window.__pdf?String(window.__pdf.length):'')")
            if state == "ERR":
                return None
            if state.isdigit():
                length = int(state)
                break
            time.sleep(1)
        if not length:
            return None
        parts = []
        for off in range(0, length, CHUNK):
            chunk = self.js_soft(f"window.__pdf.substr({off},{CHUNK})", timeout=120)
            if not chunk:
                return None       # a dropped chunk would decode to a corrupt file
            parts.append(chunk)
        try:
            return base64.b64decode("".join(parts))
        except Exception:
            return None


def nav_then_fetch(tab, url, timeout=20):
    """PDF bytes for `url` read through the browser, or None."""
    tab.nav(url)
    if not tab.wait_for_pdf(timeout):
        return None
    data = tab.read_pdf_bytes()
    return data if data and data[:4] == b"%PDF" else None


def landing_pdf_urls(tab, doi, timeout=25):
    """PDF URLs advertised by the publisher's landing page for a DOI."""
    tab.nav(f"https://doi.org/{doi}")
    if not tab.wait_for_load(timeout, off_host="doi.org"):
        return []
    urls = []
    meta = tab.js("(document.querySelector('meta[name=citation_pdf_url]')||{}).content||''")
    if meta.startswith("http"):
        urls.append(meta)
    urls += elsevier_pdf_urls(tab.js("location.href"))
    return list(dict.fromkeys(urls))


# ---- EBSCOhost: search by DOI -> viewer -> signed content URL -> curl --------
RID_JS = ("(function(){var a=Array.from(document.querySelectorAll('a')).find("
          "function(e){return /\\/search\\/details\\//.test(e.href)});if(!a)return 'NONE';"
          "var m=a.href.match(/\\/details\\/([a-z0-9]+)/i);return m?m[1]:'NONE';})()")
# The signed content URL shows up in the viewer's resource timings.
CONTENT_JS = ("(function(){var res=performance.getEntriesByType('resource')"
              ".map(function(r){return r.name});"
              "var c=res.find(function(n){return /content\\.ebscohost\\.com\\/cds\\/retrieve/.test(n)});"
              "return c||'NONE';})()")


def fetch_ebsco(tab, doi, profile, all_db=False):
    """PDF bytes for a DOI from EBSCOhost, or None.

    PsycInfo first, then every database. No record id means EBSCO does not index
    the paper; a record with no content URL means EBSCO has a link-out to the
    publisher rather than a hosted PDF.
    """
    base = f"https://research.ebsco.com/c/{profile}"
    rid = None
    for dbq in ["&db=psyh"] + ([""] if all_db else []):
        tab.nav(f"{base}/search/results?q={quote(doi, safe='')}{dbq}")
        rid = tab.wait_for_js(RID_JS, timeout=25)
        if rid:
            break
    if not rid:
        return None

    tab.nav(f"{base}/viewer/pdf/{rid}")
    url = tab.wait_for_js(CONTENT_JS, timeout=30)
    if not url:
        return None

    # The signed cds/retrieve token authenticates the request by itself.
    r = subprocess.run(["curl", "-sL", "-A", UA, url], capture_output=True, timeout=180)
    data = r.stdout
    return data if data[:4] == b"%PDF" else None


# ---- cookie-jar route -------------------------------------------------------
def export_cookies():
    subprocess.run(BU + ["cookies", "export", str(COOKIES)], capture_output=True, timeout=90)
    return COOKIES.exists()


def cookie_jar():
    """Domain-aware jar — requests sends ONLY the target host's cookies (avoids 400)."""
    raw = json.load(open(COOKIES))
    cks = raw if isinstance(raw, list) else raw.get("cookies", [])
    jar = requests.cookies.RequestsCookieJar()
    for c in cks:
        if "name" in c and "value" in c:
            try:
                jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                pass
    return jar


def fetch_direct(doi):
    """PDF bytes from a constructable publisher URL plus browser cookies, or None."""
    if not COOKIES.exists():
        export_cookies()
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/pdf,*/*"})
    s.cookies = cookie_jar()
    for u in pdf_urls(doi):
        try:
            r = s.get(u, timeout=90, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content
    return None


# ---- CLI --------------------------------------------------------------------
def _identity():
    """download_paper's Crossref lookup and identity check."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import download_paper
    return download_paper.crossref_metadata, download_paper.verify_identity


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", nargs="?", default="fetch")
    ap.add_argument("--doi", default="")
    ap.add_argument("--ebsco", action="store_true", help="use the EBSCO route (needs --doi)")
    ap.add_argument("--all-db", action="store_true",
                    help="EBSCO: search all databases, not just PsycInfo")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.mode == "export-cookies":
        print("exported" if export_cookies() else "failed", COOKIES)
        return
    if not args.doi:
        raise SystemExit("--doi is required (EBSCO is searched by DOI, not title).")

    profile = os.environ.get("INSTITUTION_EBSCO_PROFILE", "")
    if args.ebsco and not profile:
        raise SystemExit("Set INSTITUTION_EBSCO_PROFILE to your library's EBSCO cluster id "
                         "(the <cluster> in research.ebsco.com/c/<cluster>/...).")
    if args.ebsco and not chrome_running():
        raise SystemExit("The EBSCO route drives the running Chrome — start Chrome first.")

    data = None
    if args.ebsco:
        with ChromeTab() as tab:
            data = fetch_ebsco(tab, args.doi, profile, all_db=args.all_db)
    else:
        data = fetch_direct(args.doi)
        if data is None and profile and chrome_running():
            with ChromeTab() as tab:
                data = fetch_ebsco(tab, args.doi, profile, all_db=args.all_db)
    if not data:
        raise SystemExit(1)

    CACHE.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else CACHE / (hashlib.md5(args.doi.encode()).hexdigest() + ".pdf")
    out.write_bytes(data)
    crossref_metadata, verify_identity = _identity()
    meta = crossref_metadata(args.doi)
    verdict = verify_identity(str(out), args.doi, meta.get("title"), meta.get("pages"))
    if not verdict.ok:
        out.unlink()
        print(f"{verdict.state}: {verdict.reason}", file=sys.stderr)
        raise SystemExit(1)
    print(out)            # success: path on stdout (matches download_paper.py)


if __name__ == "__main__":
    main()
