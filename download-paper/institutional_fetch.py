"""
Institutional (paywalled) PDF fetch using the user's logged-in real Chrome.

For papers behind a subscription the user's institution has (e.g. via OpenAthens),
the reliable path is: log in once in the real browser, export the session cookies,
then fetch the publisher PDF directly by its URL with a DOMAIN-AWARE cookie jar.
Falls back to EBSCOhost for content reachable only through the library platform
(APA PsycInfo, plus Springer / Cloudflare-blocked psych papers EBSCO indexes).

Prereq: the user is logged into the publisher / EBSCO in `browser-use --browser real`.

Usage:
  python institutional_fetch.py --doi 10.1080/00224545.2024.2439953 [--out out.pdf]
  python institutional_fetch.py --doi 10.1037/edu0000827 --title "..." --ebsco
  python institutional_fetch.py export-cookies         # after logging in

Key gotcha learned the hard way: a full browser cookie set is thousands of cookies;
sending all of them to one host returns "400 Request Header Or Cookie Too Large".
Build a per-domain jar (requests then sends only the matching host's cookies).
"""
from __future__ import annotations
import json, subprocess, argparse, re, time, os, hashlib
from pathlib import Path
import requests

BU = ["browser-use", "--browser", "real"]
CACHE = Path.home() / ".claude" / "cache" / "pdfs"
COOKIES = Path.home() / ".claude" / "cache" / "browser_cookies.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Institution-specific EBSCO entry point. Set INSTITUTION_EBSCO_HOME to the
# research.ebsco.com/c/<cluster>/... URL your library lands on after login; the
# fallback below is one specific institution's and will not work for yours.
EBSCO_HOME = os.environ.get("INSTITUTION_EBSCO_HOME",
                            "https://research.ebsco.com/c/sxb26o/search?db=psyh")


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


# ---- EBSCOhost (drive the Next.js app via eval; the content.ebscohost token URL is the PDF) ----
def _ev(js, timeout=60):
    r = subprocess.run(BU + ["eval", js], capture_output=True, text=True, timeout=timeout)
    i = r.stdout.find("result:")
    return r.stdout[i + 7:].strip() if i != -1 else r.stdout.strip()


def fetch_ebsco(title, out):
    subprocess.run(BU + ["--headed", "open", EBSCO_HOME], capture_output=True, timeout=70)
    time.sleep(4)
    search = '''(() => {
      function d(s,r=document,o=[]){try{r.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}r.querySelectorAll("*").forEach(e=>{if(e.shadowRoot)d(s,e.shadowRoot,o)});return o;}
      const i=d("input").find(x=>/search/i.test((x.getAttribute("placeholder")||"")+(x.getAttribute("aria-label")||"")))||d("input")[0];
      if(!i)return"NO_INPUT";const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;
      set.call(i,%s);i.dispatchEvent(new Event("input",{bubbles:true}));i.focus();
      i.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",keyCode:13,which:13,bubbles:true}));
      if(i.form&&i.form.requestSubmit)i.form.requestSubmit();return"OK";})()''' % json.dumps(title)
    if _ev(search) == "NO_INPUT":
        return False
    time.sleep(6)
    _ev('''(() => {function d(s,r=document,o=[]){try{r.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}r.querySelectorAll("*").forEach(e=>{if(e.shadowRoot)d(s,e.shadowRoot,o)});return o;}
      const b=d("button,a,[role=button]").find(e=>/access options/i.test(e.textContent||""));if(b){b.click();return"OK"}return"NO";})()''')
    time.sleep(2)
    _ev('''(() => {function d(s,r=document,o=[]){try{r.querySelectorAll(s).forEach(e=>o.push(e))}catch(e){}r.querySelectorAll("*").forEach(e=>{if(e.shadowRoot)d(s,e.shadowRoot,o)});return o;}
      const o=d("a,button,[role=menuitem],[role=option],li").filter(e=>/^\\s*pdf\\s*$/i.test((e.textContent||"").trim()));
      if(o.length){try{performance.clearResourceTimings()}catch(e){}o[0].click();return"OK"}return"NO";})()''')
    time.sleep(7)
    try:
        urls = json.loads(_ev('''(()=>JSON.stringify(performance.getEntriesByType("resource").map(r=>r.name).filter(n=>/cds\\/retrieve|content\\.ebscohost/i.test(n)).slice(-3)))()'''))
    except Exception:
        urls = []
    if not urls:
        return False
    export_cookies()
    s = requests.Session(); s.headers.update({"User-Agent": UA, "Referer": "https://research.ebsco.com/"})
    s.cookies = cookie_jar()
    for u in reversed(urls):
        try:
            r = s.get(u, timeout=90, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.content[:4] == b"%PDF" and to_pdf(r.content, out):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="fetch")
    ap.add_argument("--doi", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--ebsco", action="store_true", help="use EBSCO route (needs --title)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.mode == "export-cookies":
        print("exported" if export_cookies() else "failed", COOKIES); return
    CACHE.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else CACHE / (hashlib.md5((args.doi or args.title).encode()).hexdigest() + ".pdf")
    ok = False
    if args.ebsco or not args.doi:
        ok = fetch_ebsco(args.title or args.doi, out)
    else:
        ok = fetch_direct(args.doi, out) or (args.title and fetch_ebsco(args.title, out))
    if ok:
        print(out)            # success: path on stdout (matches download_paper.py)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
