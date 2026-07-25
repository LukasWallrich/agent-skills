"""
Institutional (paywalled) PDF fetch using the user's logged-in real Chrome.

Two routes, both riding the user's own authenticated browser session:

  1. EBSCOhost (--ebsco) — best for psychology (APA PsycInfo, and Springer /
     Cloudflare-blocked papers EBSCO indexes). macOS only: it drives the real
     Chrome via osascript. Search BY DOI, read the record id, open the PDF
     viewer, take the signed content.ebscohost.com URL out of the viewer's
     resource timings, and curl it. That signed URL is SELF-AUTHENTICATING —
     no cookies, no CORS dance.
  2. Direct publisher PDF — construct the publisher's canonical PDF URL and
     fetch it with a DOMAIN-AWARE cookie jar exported from the browser.

Prereqs: log in once at the publisher / EBSCO in your real Chrome (via your
institution / OpenAthens). For route 2 also run `export-cookies` afterwards.

Usage:
  python institutional_fetch.py --doi 10.1037/edu0000827 --ebsco [--out out.pdf]
  python institutional_fetch.py --doi 10.1080/00224545.2024.2439953
  python institutional_fetch.py export-cookies         # after logging in

Set INSTITUTION_EBSCO_PROFILE to your library's cluster id — the <cluster> in the
research.ebsco.com/c/<cluster>/... URL you land on after logging in.

Gotchas learned the hard way:
  - A full browser cookie set is thousands of cookies; sending all of them to one
    host returns "400 Request Header Or Cookie Too Large". Build a per-domain jar
    so requests sends only the matching host's cookies.
  - Cross-origin fetch of a publisher PDF is CORS-blocked. Navigate to it first
    (see nav_then_fetch) so the asset host becomes same-origin.
  - Headless browsers are flagged by Cloudflare at most publishers, which is why
    everything here goes through the real, visible browser.
"""
from __future__ import annotations
import json, subprocess, argparse, re, time, os, sys, hashlib
from pathlib import Path
from urllib.parse import quote
import requests

BU = ["browser-use", "--browser", "real"]
CACHE = Path.home() / ".claude" / "cache" / "pdfs"
COOKIES = Path.home() / ".claude" / "cache" / "browser_cookies.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Your library's EBSCO cluster id (the <cluster> in research.ebsco.com/c/<cluster>/...).
EBSCO_PROFILE = os.environ.get("INSTITUTION_EBSCO_PROFILE", "")


def pdf_urls(doi):
    """Constructable publisher PDF URLs by DOI prefix (subscribed access via cookies)."""
    p = (doi or "").split("/")[0]
    t = {
        "10.1007": [f"https://link.springer.com/content/pdf/{doi}.pdf"],
        "10.1057": [f"https://link.springer.com/content/pdf/{doi}.pdf"],
        "10.1111": [f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true",
                    f"https://onlinelibrary.wiley.com/doi/pdf/{doi}"],
        "10.1002": [f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true"],
        "10.1080": [f"https://www.tandfonline.com/doi/pdf/{doi}?download=true"],
        "10.1177": [f"https://journals.sagepub.com/doi/pdf/{doi}"],
        "10.1098": [f"https://royalsocietypublishing.org/doi/pdf/{doi}"],
        "10.1371": [f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"],
    }.get(p, [])
    m = re.search(r"ssrn\.(\d+)", doi or "", re.I)
    if m:
        i = m.group(1)
        t += [f"https://papers.ssrn.com/sol3/Delivery.cfm/{i}.pdf?abstractid={i}&mirid=1"]
    return t


def is_fulltext(t):
    if len(t) >= 12000:
        return True
    low = t.lower()
    return len(t) >= 5000 and sum(s in low for s in
        ("introduction", "method", "results", "discussion", "references")) >= 3


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


def to_pdf(content, out):
    out.write_bytes(content)
    txt = out.with_suffix(".txt")
    subprocess.run(["pdftotext", "-q", str(out), str(txt)], timeout=120)
    return txt.exists() and is_fulltext(txt.read_text(errors="ignore"))


def fetch_direct(doi, out):
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
        if r.status_code == 200 and r.content[:4] == b"%PDF" and to_pdf(r.content, out):
            return True
    return False


# ---------------------------------------------------------------------------
# Real-Chrome driver (macOS / osascript).
#
# browser-use's eval route stopped working reliably against these apps, so the
# EBSCO flow talks to the visible Chrome window directly through AppleScript.
# Requires: Chrome running, and View > Developer > "Allow JavaScript from Apple
# Events" enabled once.
# ---------------------------------------------------------------------------
def _osa(script, timeout=60):
    r = subprocess.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=timeout)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def chrome_js(js):
    """Run JS in Chrome's active tab and return its result as a string."""
    esc = js.replace("\\", "\\\\").replace('"', '\\"')
    out, _ = _osa('tell application "Google Chrome" to return '
                  f'execute (active tab of front window) javascript "{esc}"')
    return out


def chrome_nav(url):
    _osa('tell application "Google Chrome" to set URL of (active tab of front window) '
         f'to "{url}"')


def chrome_open_tab():
    _osa('tell application "Google Chrome" to make new tab at end of tabs of front window '
         'with properties {URL:"about:blank"}')
    _osa('tell application "Google Chrome" to set active tab index of front window '
         'to (count of tabs of front window)')


def nav_then_fetch(url, out, wait=7):
    """Navigate Chrome to a PDF URL, then fetch it same-origin from that page.

    For publishers whose direct fetch 403s or returns HTML (Elsevier /pdfft,
    OUP /article-pdf): once Chrome has followed the redirect to the asset host,
    that host is same-origin, so an in-page fetch is allowed and credentialed.
    """
    chrome_nav(url)
    time.sleep(wait)
    if "application/pdf" not in (chrome_js("document.contentType") or ""):
        return False
    b64 = chrome_js(
        '(async()=>{const r=await fetch(location.href,{credentials:"include"});'
        'const b=new Uint8Array(await r.arrayBuffer());let s="";'
        'for(const c of b)s+=String.fromCharCode(c);return btoa(s);})()')
    if not b64:
        return False
    import base64
    try:
        data = base64.b64decode(b64)
    except Exception:
        return False
    return data[:4] == b"%PDF" and to_pdf(data, out)


# ---- EBSCOhost: search by DOI -> viewer -> signed content URL -> curl --------
RID_JS = ("(function(){var a=Array.from(document.querySelectorAll('a')).find("
          "function(e){return /\\/search\\/details\\//.test(e.href)});if(!a)return 'NONE';"
          "var m=a.href.match(/\\/details\\/([a-z0-9]+)/i);return m?m[1]:'NOID';})()")
# The old linkprocessor/v2-pdf-full-text selector is gone since the viewer
# rewrite — the signed content URL now shows up directly in resource timings.
CONTENT_JS = ("(function(){var res=performance.getEntriesByType('resource')"
              ".map(function(r){return r.name});"
              "var c=res.find(function(n){return /content\\.ebscohost\\.com\\/cds\\/retrieve/.test(n)});"
              "return c||'NONE';})()")


def fetch_ebsco(doi, out, db="psyh"):
    """Fetch a paper's PDF via EBSCOhost, by DOI. db=None searches all databases."""
    if sys.platform != "darwin":
        raise SystemExit("The EBSCO route drives Chrome via osascript — macOS only.")
    if not EBSCO_PROFILE:
        raise SystemExit("Set INSTITUTION_EBSCO_PROFILE to your library's EBSCO cluster id "
                         "(the <cluster> in research.ebsco.com/c/<cluster>/...).")
    base = f"https://research.ebsco.com/c/{EBSCO_PROFILE}"
    chrome_open_tab()

    rid = None
    for dbq in (f"&db={db}" if db else "", ""):
        chrome_nav(f"{base}/search/results?q={quote(doi, safe='')}{dbq}")
        time.sleep(7)
        got = chrome_js(RID_JS)
        if got and got not in ("NONE", "NOID"):
            rid = got
            break
    if not rid:
        return False               # not indexed here

    chrome_nav(f"{base}/viewer/pdf/{rid}")
    time.sleep(9)
    url = chrome_js(CONTENT_JS)
    if not url or url == "NONE":
        return False               # EBSCO has "Linked Full Text" only, no hosted PDF

    # The signed cds/retrieve token authenticates the request by itself.
    r = subprocess.run(["curl", "-sL", "-A", UA, "-o", str(out), url],
                       capture_output=True, timeout=180)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 20000:
        if out.exists():
            out.unlink()
        return False
    if out.read_bytes()[:4] != b"%PDF":
        out.unlink()
        return False
    return verify_pdf(out, doi)


def verify_pdf(path, doi=None, title=None):
    """Check page 1 really is the requested paper.

    Worth doing every time: title-based search tiers happily return a
    topically-similar paper under the DOI you asked for, and a mislabelled PDF
    silently corrupts whatever dataset it lands in.
    """
    txt = path.with_suffix(".txt")
    subprocess.run(["pdftotext", "-q", "-l", "2", str(path), str(txt)], timeout=120)
    if not txt.exists():
        return True                # can't check; caller may verify by reading it
    head = txt.read_text(errors="ignore").lower()
    if doi and doi.lower() in head:
        return True
    if title:
        toks = [w for w in re.findall(r"[a-z]{5,}", title.lower())][:6]
        if toks and sum(t in head for t in toks) >= max(2, len(toks) // 2):
            return True
    return not (doi or title)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="fetch")
    ap.add_argument("--doi", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--ebsco", action="store_true", help="use the EBSCO route (needs --doi)")
    ap.add_argument("--all-db", action="store_true", help="EBSCO: search all databases, not just PsycInfo")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.mode == "export-cookies":
        print("exported" if export_cookies() else "failed", COOKIES); return
    if not args.doi:
        raise SystemExit("--doi is required (EBSCO is searched by DOI, not title).")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else CACHE / (hashlib.md5(args.doi.encode()).hexdigest() + ".pdf")
    db = None if args.all_db else "psyh"
    if args.ebsco:
        ok = fetch_ebsco(args.doi, out, db=db)
    else:
        ok = fetch_direct(args.doi, out) or fetch_ebsco(args.doi, out, db=db)
    if ok:
        print(out)            # success: path on stdout (matches download_paper.py)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
