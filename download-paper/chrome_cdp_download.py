#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fetchpdf @ git+https://github.com/The-Metascience-Observatory/fetchpdf@82a4c474178a3e4245da82542c3a25434674e48e",
#   "playwright>=1.44",
#   "requests",
# ]
# ///
"""Download paywalled PDFs by DOI through the user's real Chrome sign-ins —
without taking exclusive control of Chrome.

Why this exists
---------------
The osascript route (institutional_fetch.py) only works when the user's Chrome
is the *newest* Google Chrome.app instance: Apple Events reach that one and no
other. Any Playwright/Chrome-for-Testing instance launched by another agent
therefore blocks it, even though the real profile is idle.

This script sidesteps that. It copies Chrome's cookie jar into a throwaway
`--user-data-dir`, launches that copy as its own instance (headed, offscreen)
with a debugging port, and drives it over CDP with Playwright. Because the copy
is another Chrome process, Chrome itself decrypts the cookies (Chrome 127+
cookies cannot be decrypted in Python), and the user's own window is never
touched. Everything then runs in a normal browser session, which publishers
serve where they often refuse `requests` and headless browsers.

Experimental: check the publisher terms and library licences that apply to you
first (see SKILL.md).

Access is switched on with the institution's OpenAthens redirector:
    https://go.openathens.net/redirector/<institution-domain>?url=<target>
One hop, with no per-publisher SSO dialog. Confirmed on Elsevier only, and it
reaches only what the institution subscribes to.

Usage
-----
    chrome_cdp_download.py --doi 10.1016/j.lindif.2026.102967 --out ./pdfs
    chrome_cdp_download.py --doi-file dois.txt --out ./pdfs
    INSTITUTION_OPENATHENS_DOMAIN=example.ac.uk chrome_cdp_download.py --doi ...

Every PDF passes download_paper.py's identity check against the Crossref title.
A DOI with no title (not in Crossref and no --title) is not fetched.

Success prints the path of a verified PDF (one per DOI). Exit code 0 if every
DOI produced a file, 1 otherwise, 2 if this machine cannot run the route: it
needs Chrome or Chromium, a profile to copy and a display (macOS and Linux).
Diagnostics go to stderr. A PDF already in
--out is re-checked rather than trusted, and does not count towards the cap, so
a rerun picks up where the last one stopped.

While this runs, its Chrome is the newest instance, so institutional_fetch.py
and download_paper.py's Chrome step cannot reach the user's Chrome.

Options
-------
--institution DOMAIN   OpenAthens redirector slug, e.g. `example.ac.uk`. Defaults to
                       $INSTITUTION_OPENATHENS_DOMAIN.
--institution-name N   Name to type into Springer's picker. Defaults to
                       $INSTITUTION_NAME.
--title TITLE          Title for the identity check, with a single --doi only
                       (otherwise Crossref).
--max-fetches N        Most DOIs to fetch from publishers in one run (default 30).
                       Licences often prohibit systematic downloading.
--chrome PATH          Chrome or Chromium binary (default: found by platform).
--profile-root DIR     Directory holding the browser's profiles and `Local State`
                       (default: found by platform).
--profile DIR          Profile directory to copy (default: `Default`).
--keep-profile         Do not delete the throwaway profile (debugging).
--no-verify            Keep the first PDF fetched without an identity check; the
                       printed path is then not verified.
--springer-wayf        Try Springer's WAYF institution picker when access is
                       refused (needs a real click; works only when entitled).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlparse, urljoin
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_paper as dp

#: (browser, profile root) to look for on each platform, in order.
BROWSERS = {
    "darwin": [("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                Path.home() / "Library/Application Support/Google/Chrome")],
    "linux": [(name, Path.home() / ".config" / folder)
              for name, folder in (("google-chrome", "google-chrome"),
                                   ("google-chrome-stable", "google-chrome"),
                                   ("chromium", "chromium"),
                                   ("chromium-browser", "chromium"))],
}
UNAVAILABLE = 2
DEFAULT_MAX_FETCHES = 30
#: Hosts and paths of the sign-in hops between a redirector and the publisher.
LOGIN_MARKERS = ("openathens", "idp.", "saml", "login.", "wayf")

#: Publisher PDF URLs that can be built from the DOI alone. Inline first.
TEMPLATES = {
    "10.1177": ["https://journals.sagepub.com/doi/pdf/{d}?download=true"],
    "10.1007": ["https://link.springer.com/content/pdf/{d}.pdf"],
    "10.1057": ["https://link.springer.com/content/pdf/{d}.pdf"],
    "10.1080": ["https://www.tandfonline.com/doi/pdf/{d}?needAccess=true"],
    "10.1002": ["https://onlinelibrary.wiley.com/doi/pdfdirect/{d}?download=true",
                "https://onlinelibrary.wiley.com/doi/pdf/{d}"],
    "10.1111": ["https://onlinelibrary.wiley.com/doi/pdfdirect/{d}?download=true",
                "https://onlinelibrary.wiley.com/doi/pdf/{d}"],
    "10.1108": ["https://www.emerald.com/insight/content/doi/{d}/full/pdf?download=true"],
    "10.1027": ["https://econtent.hogrefe.com/doi/pdf/{d}"],
    "10.1152": ["https://journals.physiology.org/doi/pdf/{d}"],
    "10.3389": ["https://www.frontiersin.org/articles/{d}/pdf"],
}

DOI_RX = re.compile(r"^10\.\d{4,9}/\S+$")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def env_value(name, default=""):
    """Environment first, then the skill's key file (same contract as the other scripts)."""
    if os.environ.get(name):
        return os.environ[name]
    path = Path.home() / ".claude" / "api_keys.env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


# ---------------------------------------------------------------------------
# Chrome + profile
# ---------------------------------------------------------------------------
def find_browser(args):
    """(binary, profile root) of a browser this route can use, or None if there is none."""
    if sys.platform == "linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        log("This route needs a display: it runs a headed browser, because publishers "
            "often refuse headless ones.")
        return None
    for binary, root in BROWSERS.get(sys.platform, [(None, None)]):
        binary, root = args.chrome or binary, args.profile_root or root
        if binary and root and shutil.which(binary) and (Path(root) / args.profile).is_dir():
            return shutil.which(binary), Path(root)
    log(f"No Chrome or Chromium with a `{args.profile}` profile found on this machine. "
        "Pass --chrome and --profile-root if they are elsewhere.")
    return None


def copy_profile(root: Path, profile: str, dest: Path):
    """Copy only what Chrome needs to hold the cookie jar.

    Cookies live in `Default/Cookies` on some builds and `Default/Network/Cookies`
    on others; Local State carries the key material, so it must come too.
    """
    src = root / profile
    if not src.is_dir():
        raise SystemExit(f"Browser profile not found: {src}")
    dst = dest / "Default"
    (dst / "Network").mkdir(parents=True, exist_ok=True)
    state = root / "Local State"
    if state.exists():
        shutil.copy2(state, dest / "Local State")
    for cand, target in [(src / "Cookies", dst / "Cookies"),
                         (src / "Network/Cookies", dst / "Network/Cookies"),
                         (src / "Preferences", dst / "Preferences")]:
        if cand.exists():
            shutil.copy2(cand, target)
    if not (dst / "Cookies").exists() and not (dst / "Network/Cookies").exists():
        raise SystemExit("No cookie database in the Chrome profile; is Chrome signed in?")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(profile_dir: Path, chrome: str, port: int):
    args = [chrome,
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={port}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync",
            "--window-position=-3000,-3000", "--window-size=1280,900",
            "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            body = urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            if b"Browser" in body:
                return proc
        except Exception:
            time.sleep(0.3)
    proc.terminate()
    raise SystemExit("Chrome did not open a debugging port")


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------
def at_login(url):
    """Whether `url` is a sign-in hop. The query is ignored: it can quote the target."""
    parts = urlparse(url)
    return any(k in parts.netloc + parts.path for k in LOGIN_MARKERS)


def settle(page, max_s=25):
    """Wait out sign-in hops and script redirects, until the URL holds still."""
    t0 = time.time()
    last = None
    while time.time() - t0 < max_s:
        url = page.url
        if url == last and not at_login(url):
            break
        last = url
        page.wait_for_timeout(2500)


def evaluate(page, script):
    """`page.evaluate`, retried when a redirect destroys the page under it."""
    for tries_left in (2, 1, 0):
        try:
            return page.evaluate(script)
        except Exception as e:
            if not tries_left or "context was destroyed" not in str(e):
                raise
            page.wait_for_timeout(2500)


def redirector(institution, target):
    return f"https://go.openathens.net/redirector/{institution}?url={quote(target, safe='')}"


def is_pdf(b):
    return bool(b) and b[:5] == b"%PDF-" and len(b) > 20_000


def capture_pdf(page, url, wait=4500, timeout=90_000):
    """Navigate to `url` and return the PDF bytes, from the viewer or a download."""
    responses, downloads = [], []

    def on_response(r):
        try:
            ct = (r.headers.get("content-type") or "").lower()
            if "pdf" in ct or r.url.lower().split("?")[0].endswith(".pdf"):
                responses.append(r)
        except Exception:
            pass

    def on_download(d):
        downloads.append(d)

    page.on("response", on_response)
    page.on("download", on_download)
    try:
        page.goto(url, wait_until="load", timeout=timeout)
    except Exception:
        pass                      # a download aborts navigation; bytes may still arrive
    page.wait_for_timeout(wait)
    for d in downloads:
        try:
            p = d.path()
            if p and os.path.exists(p):
                b = Path(p).read_bytes()
            else:
                with tempfile.TemporaryDirectory() as td:
                    tmp = Path(td) / "download.pdf"
                    d.save_as(tmp)
                    b = tmp.read_bytes()
            if is_pdf(b):
                return _stop(page, on_response, on_download, b)
        except Exception:
            pass
    for r in reversed(responses):
        try:
            b = r.body()
            if is_pdf(b):
                return _stop(page, on_response, on_download, b)
        except Exception:
            pass
    return _stop(page, on_response, on_download, None)


def _stop(page, on_response, on_download, value):
    for fn in (on_response, on_download):
        try:
            page.remove_listener("response" if fn is on_response else "download", fn)
        except Exception:
            pass
    return value


def unlock(page, logf):
    """Click a publisher's inline institutional-access control, if it has one.

    Elsevier puts *Access through <institution>* on the abstract page; one click
    moves the session to the full article (and its `/pdfft` URL). SAGE/T&F/Wiley
    show the same label as a modal opener; clicking it there is harmless.
    """
    clicked = evaluate(
        page,
        """()=>{
      const rx=/access through|brought to you by|log in via an institution|institutional (access|login|sign)/i;
      for(const e of document.querySelectorAll('a,button,strong,span')){
        const t=(e.innerText||'').trim();
        if(t && t.length<90 && rx.test(t)){ (e.closest('a')||e).click(); return t; }
      }
      return null}""")
    if clicked:
        logf(f"  clicked: {clicked[:60]}")
        page.wait_for_timeout(4000)
        settle(page)
    return clicked


def pdf_links(page):
    """PDF-looking anchors, plus citation_pdf_url, resolved absolute."""
    urls = []
    try:
        meta = evaluate(
            page, "(document.querySelector('meta[name=citation_pdf_url]')||{}).content||''")
        if meta.startswith("http"):
            urls.append(meta)
    except Exception:
        pass
    try:
        anchors = evaluate(
            page, "()=>[...document.querySelectorAll('a')].map(a=>[a.getAttribute('href')||'',"
            "(a.innerText||'').trim().slice(0,60)])")
    except Exception:
        anchors = []
    for href, text in anchors:
        if not href or href.startswith(("javascript", "#", "mailto")):
            continue
        if "pdf" in href.lower() or "/download" in href.lower():
            urls.append(urljoin(page.url, href))
    return list(dict.fromkeys(urls))


def templates(doi):
    p = doi.split("/")[0]
    return [t.format(d=doi) for t in TEMPLATES.get(p, [])]


# ---------------------------------------------------------------------------
# Springer's institution picker (only when entitled)
# ---------------------------------------------------------------------------
def springer_wayf(page, doi, logf, institution_name):
    try:
        page.goto("https://wayf.springernature.com/?redirect_uri="
                  + quote(f"https://link.springer.com/article/{doi}", safe=""),
                  wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2500)
        page.click("#autocomplete-input", timeout=20_000)
        page.keyboard.type(institution_name, delay=50)
        page.wait_for_timeout(2500)
        page.click(".eds-c-autocomplete__item >> nth=0", timeout=15_000)
        page.wait_for_timeout(1200)
        page.click("#institution-submit", timeout=15_000)
        for _ in range(8):
            page.wait_for_timeout(3000)
            if "link.springer.com" in page.url and "wayf" not in page.url:
                break
        return True
    except Exception as e:
        logf(f"  springer wayf: {e}")
        return False


# ---------------------------------------------------------------------------
# One DOI
# ---------------------------------------------------------------------------
def output_path(out_dir, doi):
    return Path(out_dir) / (doi.replace("/", "_") + ".pdf")


def try_urls(page, urls, tried, doi, record, args, logf):
    """Fetch `urls` in turn until one passes the identity check and is saved."""
    out = output_path(args.out, doi)
    tmp = out.with_suffix(".part")
    for u in urls:
        if u in tried:
            continue
        tried.add(u)
        data = capture_pdf(page, u)
        if not data:
            continue
        if args.no_verify:
            tmp.write_bytes(data)
            logf(f"  kept UNVERIFIED: {u[:80]}")
        elif not dp.accept_bytes(data, str(tmp), doi, record["title"], record["pages"], u[:80]):
            continue
        tmp.replace(out)
        return True
    return False


def failure_reason(url, institution):
    """What a failed fetch says, from where the first navigation ended up."""
    if at_login(url):
        return (f"stopped at a sign-in page ({urlparse(url).netloc}): the Chrome session "
                "is missing or expired, so sign in there and re-run")
    if not institution:
        return "no institution set, so this was tried without institutional access"
    return "reached the publisher but found no PDF: likely no entitlement, so check the library's A-Z list"


def fetch_one(doi, ctx, args, record, logf):
    """`attempt`, with one page's failure kept from ending the whole batch."""
    try:
        return attempt(doi, ctx, args, record, logf)
    except Exception as e:
        logf(f"  stopped by a page error: {str(e).splitlines()[0][:120]}")
        return False


def attempt(doi, ctx, args, record, logf):
    """Fetch one DOI into --out. Returns True when its file was saved."""
    page = ctx.new_page()
    page.set_default_timeout(60_000)
    try:
        landing = "https://doi.org/" + doi
        if args.institution:
            page.goto(redirector(args.institution, landing),
                      wait_until="domcontentloaded", timeout=90_000)
        else:
            page.goto(landing, wait_until="domcontentloaded", timeout=90_000)
        settle(page)
        arrived = page.url
        logf(f"  at {arrived[:90]}")
        unlock(page, logf)

        tried = set()
        if try_urls(page, pdf_links(page) + templates(doi), tried, doi, record, args, logf):
            return True

        # Nothing on the article page: go back and look at the landing page.
        page.goto(landing, wait_until="domcontentloaded", timeout=90_000)
        settle(page)
        unlock(page, logf)
        if try_urls(page, pdf_links(page) + templates(doi), tried, doi, record, args, logf):
            return True

        if args.springer_wayf and doi.startswith(("10.1007/", "10.1057/")):
            logf("  trying Springer WAYF picker")
            # A fresh `tried`: the same URLs can answer differently once signed in.
            if (springer_wayf(page, doi, logf, args.institution_name) and
                    try_urls(page, pdf_links(page) + templates(doi), set(), doi, record, args, logf)):
                return True
        logf(f"  {failure_reason(arrived, args.institution)}")
        return False
    finally:
        try:
            page.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def read_dois(args):
    dois = list(args.doi)
    if args.doi_file:
        dois += [l.split(",")[0].strip() for l in Path(args.doi_file).read_text().splitlines()
                 if l.strip() and not l.lower().startswith("doi")]
    kept = []
    for raw in dois:
        doi = dp.clean_doi(raw.strip())
        if DOI_RX.match(doi):
            kept.append(doi)
        else:
            log(f"Ignoring {raw!r}: not a DOI")
    return list(dict.fromkeys(kept))


def plan(dois, args):
    """Sort DOIs without touching a publisher.

    Returns (present paths, DOIs with no title, [(doi, identity record)] to fetch).
    """
    present, untitled, pending = [], [], []
    for doi in dois:
        meta = dp.crossref_metadata(doi)
        record = {"title": meta.get("title") or args.title or None, "pages": meta.get("pages")}
        out = output_path(args.out, doi)
        if not record["title"] and not args.no_verify:
            log(f"== {doi}\n  no title from Crossref, so it cannot be verified; pass --title")
            untitled.append(doi)
        elif out.exists() and out.stat().st_size > 20_000 and (
                args.no_verify or dp.verify_identity(str(out), doi, record["title"], record["pages"]).ok):
            log(f"== {doi}\n  already present")
            present.append(out)
        else:
            pending.append((doi, record))
    return present, untitled, pending


def fetch_all(pending, args, browser):
    """Run Chrome for `pending`. Returns how many DOIs produced no file.

    Python's default SIGTERM skips `finally`, which would leave the cookie copy on
    disk and a Chrome that blocks the osascript route, and agent timeouts send
    SIGTERM. The handler exits directly: it can fire inside Playwright's greenlets,
    where raising an exception would not reliably unwind this function.
    """
    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="chrome-cdp-"))
    procs = []

    def cleanup():
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
                proc.wait()
        if args.keep_profile:
            log(f"profile kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    def on_signal(signum, frame):
        cleanup()
        os._exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, on_signal)

    failures = 0
    try:
        binary, root = browser
        copy_profile(root, args.profile, work)
        port = free_port()
        procs.append(launch(work, binary, port))
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            for doi, record in pending:
                log(f"== {doi}")
                if fetch_one(doi, ctx, args, record, log):
                    print(output_path(args.out, doi), flush=True)
                else:
                    failures += 1
                    log("  no verified PDF")
            try:
                browser.close()
            except Exception:
                pass
    finally:
        cleanup()
    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doi", action="append", default=[])
    ap.add_argument("--doi-file")
    ap.add_argument("--out", default=".")
    ap.add_argument("--institution", default=env_value("INSTITUTION_OPENATHENS_DOMAIN"))
    ap.add_argument("--institution-name", default=env_value("INSTITUTION_NAME"))
    ap.add_argument("--title", default="")
    ap.add_argument("--max-fetches", type=int, default=DEFAULT_MAX_FETCHES)
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--profile-root", default=None)
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--keep-profile", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--springer-wayf", action="store_true")
    args = ap.parse_args()

    dois = read_dois(args)
    if not dois:
        raise SystemExit("No DOIs supplied (--doi / --doi-file).")
    if args.title and len(dois) > 1:
        raise SystemExit("--title applies to one DOI; with several, titles come from Crossref.")
    if args.springer_wayf and not args.institution_name:
        raise SystemExit("--springer-wayf needs --institution-name or INSTITUTION_NAME.")
    Path(args.out).mkdir(parents=True, exist_ok=True)

    present, untitled, pending = plan(dois, args)
    for out in present:
        print(out, flush=True)
    to_fetch, over_cap = pending[:args.max_fetches], pending[args.max_fetches:]
    failures = 0
    if to_fetch:
        browser = find_browser(args)
        if not browser:
            sys.exit(UNAVAILABLE)
        failures = fetch_all(to_fetch, args, browser)
    if over_cap:
        log(f"Stopped at --max-fetches {args.max_fetches}: {len(over_cap)} DOIs not attempted. "
            "Re-running continues from files saved so far, but retries the failures first; "
            "raising the cap is a licence question for the user, not a routine retry.")
    sys.exit(0 if not (failures or untitled or over_cap) else 1)


if __name__ == "__main__":
    main()
