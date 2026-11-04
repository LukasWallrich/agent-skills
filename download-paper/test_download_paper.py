"""The web-search, publisher-API, Google Scholar and Chrome steps, with network and Chrome mocked.

Run with:
    cd download-paper && uv run --no-project --with pytest --with requests \
      pytest test_download_paper.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_paper as dp

PDF = b"%PDF-1.4" + b"x" * 2000
BLOCKED_HTML = b"<html>Our systems have detected unusual traffic</html>"

WRONG_URL = "https://example.org/other-paper.pdf"
RIGHT_URL = "https://osf.io/download/abc/"
BLOCKED_URL = "https://psycnet.apa.org/fulltext/2018-1.pdf"


class Verdict:
    def __init__(self, ok, state):
        self.ok = ok
        self.state = state
        self.reason = state


@pytest.fixture
def scholar(monkeypatch):
    """Serve canned bodies and verdicts, and record the order of the walk."""
    bodies = {WRONG_URL: PDF, RIGHT_URL: PDF, BLOCKED_URL: BLOCKED_HTML}
    verdicts = {WRONG_URL: Verdict(False, "wrong_article"),
                RIGHT_URL: Verdict(True, "verified")}
    seen = []

    def fake_fetch(url, timeout=60, accept=None):
        seen.append(url)
        return bodies.get(url)

    monkeypatch.setattr(dp, "fetch_url", fake_fetch)
    monkeypatch.setattr(dp, "verify_identity",
                        lambda path, doi, title, pages: verdicts[seen[-1]])
    return seen


def test_walks_past_a_wrong_paper_to_the_right_one(scholar, tmp_path):
    tmp = str(tmp_path / "part.pdf")
    accepted, blocked, wrong = dp.walk_pdf_links(
        [BLOCKED_URL, WRONG_URL, RIGHT_URL], tmp, "10.1/x", "A Title", "1-9")

    assert accepted == RIGHT_URL
    assert blocked == [BLOCKED_URL]
    assert wrong == [WRONG_URL]
    assert scholar == [BLOCKED_URL, WRONG_URL, RIGHT_URL]
    assert dp.validate_pdf(tmp)


def test_no_right_paper_leaves_nothing_behind(scholar, tmp_path):
    tmp = str(tmp_path / "part.pdf")
    accepted, blocked, wrong = dp.walk_pdf_links(
        [WRONG_URL, BLOCKED_URL], tmp, "10.1/x", "A Title", "1-9")

    assert accepted is None
    assert blocked == [BLOCKED_URL]
    assert wrong == [WRONG_URL]
    assert not os.path.exists(tmp)


# --- The identity check: a printed DOI is not enough on its own -------------

TITLE = "Intergroup Contact and Prejudice: A Meta-Analysis"
LONG_FRONT = "x" * 500


@pytest.fixture
def fetchpdf_stub(monkeypatch):
    """Stand-in fetchpdf modules: the DOI is always found; the front text is settable."""
    import types
    state = {"front": LONG_FRONT, "fraction": 1.0, "signals": ["doi-in-text"]}

    class IdentityVerdict(Verdict):
        def __init__(self, state_, reason, signals=None):
            super().__init__(state_ == "verified", state_)
            self.reason, self.signals = reason, signals or []

    mods = {
        "fetchpdf": types.ModuleType("fetchpdf"),
        "fetchpdf.retrieval": types.ModuleType("fetchpdf.retrieval"),
        "fetchpdf.retrieval._util": types.SimpleNamespace(
            squash=lambda t: t, matched_fraction=lambda a, b: state["fraction"]),
        "fetchpdf.retrieval.pdf_identity": types.SimpleNamespace(
            WRONG="wrong_article", IdentityVerdict=IdentityVerdict,
            verify_pdf_identity=lambda *a, **k: IdentityVerdict(
                "verified", "found", state["signals"])),
        "fetchpdf.retrieval.pdf_text": types.SimpleNamespace(
            pdf_text=lambda *a, **k: state["front"]),
        "fetchpdf.retrieval.resolve": types.SimpleNamespace(arxiv_id_from_doi=lambda d: None),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return state


def test_doi_without_the_title_is_a_citing_paper(fetchpdf_stub):
    fetchpdf_stub["fraction"] = 0.52
    verdict = dp.verify_identity("x.pdf", "10.1/x", TITLE, "1-9")
    assert not verdict.ok and verdict.state == "wrong_article"


def test_doi_with_a_partly_legible_title_is_kept(fetchpdf_stub):
    fetchpdf_stub["fraction"] = 0.76          # line-numbered preprint
    assert dp.verify_identity("x.pdf", "10.1/x", TITLE, "1-9").ok


def test_doi_on_an_illegible_front_page_is_kept(fetchpdf_stub):
    fetchpdf_stub.update(fraction=0.0, front="x" * 100)
    assert dp.verify_identity("x.pdf", "10.1/x", TITLE, "1-9").ok


def test_title_acceptance_is_not_rechecked(fetchpdf_stub):
    fetchpdf_stub.update(fraction=0.0, signals=["title-on-page"])
    assert dp.verify_identity("x.pdf", "10.1/x", TITLE, "1-9").ok


# --- Step W: web search, with ddgs and the network mocked ------------------

@pytest.mark.parametrize("result_title, ok", [
    ("PDF Intergroup Contact and Prejudice: A Meta-Analysis", True),
    ("(PDF) Intergroup Contact and Prejudice: A Meta ...", True),
    ("Intergroup Contact and Prejudice: A Meta-Analysis - Uni Repository", True),
    ("PDF Intergroup Contact and Prejudice", True),
    ("PDF Introduction", False),
    ("PDF Contact and Prejudice Revisited", False),
    ("", False),
])
def test_web_title_match(result_title, ok):
    assert dp.web_title_match(result_title, TITLE) is ok


def test_web_pdf_links_keep_matched_pdfs_only():
    results = [{"title": f"PDF {TITLE}", "href": "https://a.edu/slides/x.pdf"},
               {"title": f"PDF {TITLE}", "href": "https://a.edu/landing"},
               {"title": "PDF Something Else Entirely Here", "href": "https://b.edu/y.pdf"},
               {"title": f"{TITLE} ...", "href": "https://a.edu/slides/x.pdf"}]
    assert dp.web_pdf_links(results, TITLE) == ["https://a.edu/slides/x.pdf"]


def test_web_search_failure_counts_as_no_results(monkeypatch, tmp_path):
    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("202 Ratelimit")

    monkeypatch.setitem(sys.modules, "ddgs", type(sys)("ddgs"))
    sys.modules["ddgs"].DDGS = Broken
    assert dp.web_search("10.1/x pdf", str(tmp_path)) == []
    # A failed search is not cached, so the next run searches again.
    assert not [f for f in os.listdir(tmp_path / "web-search") if f.endswith(".json")]


def test_web_search_results_are_cached(monkeypatch, tmp_path):
    calls = []

    class Fake:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results):
            calls.append(query)
            return [{"title": TITLE, "href": "https://a.edu/x.pdf", "body": "..."}]

    monkeypatch.setitem(sys.modules, "ddgs", type(sys)("ddgs"))
    sys.modules["ddgs"].DDGS = Fake
    monkeypatch.setattr(dp, "WEB_SEARCH_SPACING", 0)
    first = dp.web_search("10.1/x pdf", str(tmp_path))
    assert dp.web_search("10.1/x pdf", str(tmp_path)) == first
    assert calls == ["10.1/x pdf"]


DRAFT_URL = "https://a.edu/draft.pdf"
AUTHOR_URL = "https://b.edu/~author/paper.pdf"


@pytest.fixture
def web(monkeypatch):
    """Canned searches: the DOI query finds a draft, the title query the paper."""
    searches = {"10.1/x pdf": [{"title": f"PDF {TITLE}", "href": DRAFT_URL}],
                f'"{TITLE}" pdf': [{"title": f"PDF {TITLE}", "href": DRAFT_URL},
                                   {"title": TITLE, "href": AUTHOR_URL}]}
    monkeypatch.setattr(dp, "web_search", lambda q, cache_dir: searches.get(q, []))
    seen = []

    def fake_fetch(url, timeout=60, accept=None):
        seen.append(url)
        return PDF

    monkeypatch.setattr(dp, "fetch_url", fake_fetch)
    verdicts = {DRAFT_URL: Verdict(False, "wrong_article"),
                AUTHOR_URL: Verdict(True, "verified")}
    monkeypatch.setattr(dp, "verify_identity",
                        lambda path, doi, title, pages: verdicts[seen[-1]])
    return seen


def test_web_skips_a_draft_and_falls_back_to_the_title_query(web, tmp_path):
    tmp = str(tmp_path / "part.pdf")
    accepted, blocked = dp.web_download("10.1/x", TITLE, tmp, "1-9", str(tmp_path))

    assert accepted == AUTHOR_URL
    assert web == [DRAFT_URL, AUTHOR_URL]       # the draft is not downloaded twice
    assert dp.validate_pdf(tmp)


def test_web_finds_nothing_when_search_is_down(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "web_search", lambda q, cache_dir: [])
    monkeypatch.setattr(dp, "fetch_url", lambda *a, **k: pytest.fail("nothing to fetch"))
    tmp = str(tmp_path / "part.pdf")

    assert dp.web_download("10.1/x", TITLE, tmp, "1-9", str(tmp_path)) == (None, [])
    assert not os.path.exists(tmp)


def test_chain_continues_to_scholar_after_an_empty_web_search(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {"title": TITLE, "search_title": TITLE, "pages": "1-9"})
    monkeypatch.setattr(dp, "fetchpdf_download", lambda *a: False)
    monkeypatch.setattr(dp, "web_search", lambda q, cache_dir: [])
    monkeypatch.setattr(dp, "load_key_file", lambda: {"SERPAPI_API_KEY": "k"})
    monkeypatch.setattr(dp, "scholar_pdf_links", lambda q, keys: [RIGHT_URL])
    monkeypatch.setattr(dp, "fetch_url", lambda *a, **k: PDF)
    monkeypatch.setattr(dp, "verify_identity", lambda *a: Verdict(True, "verified"))

    path, _ = dp.download_paper("10.1/x", cache_dir=str(tmp_path), use_browser=False)

    assert path == dp.doi_to_cache_path("10.1/x", str(tmp_path))
    assert dp.validate_pdf(path)


# --- Step C: the Chrome walk, with the whole Chrome layer mocked -------------

PUBLISHER_URL = "https://journals.sagepub.com/doi/pdf/10.1/x"


class FakeTab:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def javascript_allowed(self):
        return True


@pytest.fixture
def browser(monkeypatch):
    """A Chrome that is running, serves canned bytes, and records the walk."""
    monkeypatch.setattr(dp.sys, "platform", "darwin")
    monkeypatch.setattr(dp.chrome, "chrome_running", lambda: True)
    monkeypatch.setattr(dp.chrome, "ChromeTab", FakeTab)
    monkeypatch.setattr(dp.chrome, "pdf_urls", lambda doi: [PUBLISHER_URL])
    monkeypatch.setattr(dp.chrome, "landing_pdf_urls", lambda tab, doi: [])
    seen = []

    def fake_nav_then_fetch(tab, url, timeout=20):
        seen.append(url)
        return {BLOCKED_URL: PDF, WRONG_URL: PDF, PUBLISHER_URL: PDF}.get(url)

    monkeypatch.setattr(dp.chrome, "nav_then_fetch", fake_nav_then_fetch)
    return seen


def verdicts_by_url(monkeypatch, seen, table):
    monkeypatch.setattr(dp, "verify_identity",
                        lambda path, doi, title, pages: table[seen[-1]])


def test_chrome_keeps_a_blocked_scholar_link_that_verifies(browser, monkeypatch, tmp_path):
    verdicts_by_url(monkeypatch, browser, {BLOCKED_URL: Verdict(True, "verified")})
    tmp = str(tmp_path / "part.pdf")

    kept = dp.browser_download("10.1/x", [BLOCKED_URL], tmp, "A Title", "1-9", "")

    assert kept == BLOCKED_URL
    assert browser == [BLOCKED_URL]
    assert dp.validate_pdf(tmp)


def test_chrome_walks_past_a_wrong_paper(browser, monkeypatch, tmp_path):
    verdicts_by_url(monkeypatch, browser, {BLOCKED_URL: Verdict(False, "wrong_article"),
                                           PUBLISHER_URL: Verdict(True, "verified")})
    tmp = str(tmp_path / "part.pdf")

    kept = dp.browser_download("10.1/x", [BLOCKED_URL], tmp, "A Title", "1-9", "")

    assert kept == PUBLISHER_URL
    assert browser == [BLOCKED_URL, PUBLISHER_URL]


def test_chrome_refuses_a_truncated_preview(browser, monkeypatch, tmp_path):
    verdicts_by_url(monkeypatch, browser, {BLOCKED_URL: Verdict(False, "truncated"),
                                           PUBLISHER_URL: Verdict(False, "truncated")})
    tmp = str(tmp_path / "part.pdf")

    kept = dp.browser_download("10.1/x", [BLOCKED_URL], tmp, "A Title", "1-9", "")

    assert kept is None
    assert not os.path.exists(tmp)


def test_step_is_skipped_when_chrome_is_not_running(monkeypatch, tmp_path):
    monkeypatch.setattr(dp.sys, "platform", "darwin")
    monkeypatch.setattr(dp.chrome, "chrome_running", lambda: False)
    monkeypatch.setattr(dp.chrome, "ChromeTab",
                        lambda: pytest.fail("Chrome must not be touched"))

    assert dp.browser_download("10.1/x", [], str(tmp_path / "part.pdf"),
                               "A Title", "1-9", "") is None


# --- Step P: publisher APIs, with HTTP mocked --------------------------------

WILEY_DOI = "10.1002/pits.1"
SPRINGER_DOI = "10.1186/s1-2"
SECRET = "sekret-key-123"


class Response:
    def __init__(self, status=200, content=b"", location=None):
        self.status_code = status
        self.content = content
        self.is_redirect = location is not None
        self.headers = {"Location": location} if location else {}


@pytest.fixture
def http(monkeypatch, tmp_path):
    """Queue responses for requests.get, and record every call made."""
    calls, queue = [], []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(dp.requests, "get", fake_get)
    monkeypatch.setattr(dp, "_wiley_off", False)
    monkeypatch.setattr(dp, "_springer_off", False)
    monkeypatch.setattr(dp, "WILEY_TDM_SPACING", 0)
    monkeypatch.setattr(dp, "SPRINGER_OA_SPACING", 0)
    return calls, queue


def test_wiley_keeps_the_token_off_the_redirect(http, tmp_path):
    calls, queue = http
    queue += [Response(302, location="https://alm.wiley.com/x.pdf"), Response(200, PDF)]

    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) == PDF

    assert calls[0][1]["headers"]["Wiley-TDM-Client-Token"] == SECRET
    assert calls[0][1]["allow_redirects"] is False
    assert "10.1002%2Fpits.1" in calls[0][0]
    assert calls[1][0] == "https://alm.wiley.com/x.pdf"
    assert SECRET not in repr(calls[1])


def test_wiley_403_is_not_entitled_and_the_route_stays_on(http, tmp_path, capsys):
    _, queue = http
    queue.append(Response(403))

    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) is None

    assert "not entitled" in capsys.readouterr().err
    assert dp._wiley_off is False


def test_wiley_429_switches_the_route_off(http, tmp_path):
    _, queue = http
    queue.append(Response(429))
    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) is None
    assert dp._wiley_off is True


def test_wiley_refusal_by_the_download_host_is_not_called_entitlement(http, tmp_path, capsys):
    _, queue = http
    queue += [Response(302, location="https://alm.wiley.com/x.pdf"), Response(403)]

    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) is None
    assert "not entitled" not in capsys.readouterr().err


def test_wiley_html_is_not_a_pdf(http, tmp_path):
    _, queue = http
    queue.append(Response(200, BLOCKED_HTML))
    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) is None


def test_wiley_error_text_is_redacted(http, tmp_path, capsys):
    _, queue = http
    queue.append(dp.requests.ConnectionError(f"boom token={SECRET}"))

    assert dp.wiley_tdm_pdf(WILEY_DOI, SECRET, str(tmp_path)) is None

    captured = capsys.readouterr()
    assert SECRET not in captured.err + captured.out


def jats(doi=SPRINGER_DOI, title=TITLE, body="word " * 400):
    body_xml = f"<body><p>{body}</p></body>" if body else ""
    return (f'<response><records><article><front><article-meta>'
            f'<article-id pub-id-type="publisher-id">s1</article-id>'
            f'<article-id pub-id-type="doi">{doi}</article-id>'
            f'<title-group><article-title>{title}</article-title></title-group>'
            f'<abstract><p>Short.</p></abstract></article-meta></front>{body_xml}'
            f'</article></records></response>').encode()


def test_springer_writes_only_the_verified_article(http, tmp_path, fetchpdf_stub):
    calls, queue = http
    queue.append(Response(200, jats()))

    path = dp.springer_oa_xml(SPRINGER_DOI, TITLE, SECRET, str(tmp_path))

    assert path == dp.doi_to_xml_path(SPRINGER_DOI, str(tmp_path))
    assert open(path, "rb").read().startswith(b"<article")
    assert calls[0][1]["params"] == {"q": f"doi:{SPRINGER_DOI}", "api_key": SECRET}
    assert calls[0][1]["allow_redirects"] is False
    # A second call answers from the cache.
    assert dp.springer_oa_xml(SPRINGER_DOI, TITLE, SECRET, str(tmp_path)) == path
    assert len(calls) == 1


@pytest.mark.parametrize("response, off", [
    (Response(404, b"No data was found"), False),
    (Response(500), False),
    (Response(200, b"<html>not closed"), False),
    (Response(200, b"<response><records/></response>"), False),
    (Response(200, jats(doi="10.1186/another")), False),
    (Response(200, jats(body="")), False),
    (Response(200, jats(body="too short")), False),
    (Response(401), True),
    (Response(403), True),
    (Response(429), True),
])
def test_springer_misses_leave_no_file(http, tmp_path, fetchpdf_stub, response, off):
    _, queue = http
    queue.append(response)

    assert dp.springer_oa_xml(SPRINGER_DOI, TITLE, SECRET, str(tmp_path)) is None

    assert not os.path.exists(dp.doi_to_xml_path(SPRINGER_DOI, str(tmp_path)))
    assert dp._springer_off is off


def test_springer_rejects_a_record_with_another_title(http, tmp_path, fetchpdf_stub):
    fetchpdf_stub["fraction"] = 0.2
    _, queue = http
    queue.append(Response(200, jats(title="Something else")))
    assert dp.springer_oa_xml(SPRINGER_DOI, TITLE, SECRET, str(tmp_path)) is None


def test_springer_error_text_is_redacted(http, tmp_path, capsys):
    _, queue = http
    queue.append(dp.requests.ConnectionError(f"Max retries with url: /jats?api_key={SECRET}"))

    assert dp.springer_oa_xml(SPRINGER_DOI, TITLE, SECRET, str(tmp_path)) is None

    captured = capsys.readouterr()
    assert SECRET not in captured.err + captured.out
    assert "<redacted>" in captured.err


@pytest.fixture
def walk(monkeypatch, http, fetchpdf_stub):
    """A walk where fetchpdf and the web search find nothing."""
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {"title": TITLE, "search_title": TITLE, "pages": "1-9"})
    monkeypatch.setattr(dp, "fetchpdf_download", lambda *a: False)
    monkeypatch.setattr(dp, "web_search", lambda q, cache_dir: [])
    return http


@pytest.mark.parametrize("doi", [WILEY_DOI, SPRINGER_DOI])
def test_routes_are_silent_and_make_no_call_without_a_key(walk, monkeypatch, tmp_path, capsys, doi):
    monkeypatch.setattr(dp, "load_key_file", lambda: {})

    path, _ = dp.download_paper(doi, cache_dir=str(tmp_path), use_browser=False)

    assert path is None
    assert walk[0] == []
    err = capsys.readouterr().err
    assert "Wiley" not in err and "Springer" not in err


@pytest.mark.parametrize("doi", ["10.1037/x", "10.1186/s1-2"])
def test_wiley_is_asked_only_about_its_own_dois(walk, monkeypatch, tmp_path, doi):
    monkeypatch.setattr(dp, "load_key_file", lambda: {"WILEY_TDM_TOKEN": SECRET})
    dp.download_paper(doi, cache_dir=str(tmp_path), use_browser=False)
    assert walk[0] == []


def test_wiley_pdf_goes_through_the_identity_check(walk, monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "load_key_file", lambda: {"WILEY_TDM_TOKEN": SECRET})
    monkeypatch.setattr(dp, "verify_identity", lambda *a: Verdict(False, "wrong_article"))
    walk[1].append(Response(200, PDF))

    path, _ = dp.download_paper(WILEY_DOI, cache_dir=str(tmp_path), use_browser=False)

    assert path is None
    assert not os.path.exists(dp.doi_to_cache_path(WILEY_DOI, str(tmp_path)))


def test_wiley_pdf_that_verifies_is_cached(walk, monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "load_key_file", lambda: {"WILEY_TDM_TOKEN": SECRET})
    monkeypatch.setattr(dp, "verify_identity", lambda *a: Verdict(True, "verified"))
    walk[1].append(Response(200, PDF))

    path, _ = dp.download_paper(WILEY_DOI, cache_dir=str(tmp_path), use_browser=False)

    assert path == dp.doi_to_cache_path(WILEY_DOI, str(tmp_path))
    assert dp.validate_pdf(path)


def test_springer_xml_is_a_separate_outcome_by_default(walk, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dp, "load_key_file", lambda: {"SPRINGER_OA_API_KEY": SECRET})
    walk[1].append(Response(200, jats()))

    path, _ = dp.download_paper(SPRINGER_DOI, cache_dir=str(tmp_path), use_browser=False)

    assert path is None         # the PDF contract is unchanged
    xml = dp.doi_to_xml_path(SPRINGER_DOI, str(tmp_path))
    assert f"FULLTEXT_XML={xml}" in capsys.readouterr().err


def test_accept_xml_stops_the_walk(walk, monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "load_key_file", lambda: {"SPRINGER_OA_API_KEY": SECRET})
    monkeypatch.setattr(dp, "web_search", lambda q, cache_dir: pytest.fail("walk went on"))
    walk[1].append(Response(200, jats()))

    path, _ = dp.download_paper(SPRINGER_DOI, cache_dir=str(tmp_path), use_browser=False,
                                accept_xml=True)

    assert path == dp.doi_to_xml_path(SPRINGER_DOI, str(tmp_path))
    # The next run goes straight to the cached XML.
    monkeypatch.setattr(dp, "fetchpdf_download", lambda *a: pytest.fail("not needed"))
    assert dp.download_paper(SPRINGER_DOI, cache_dir=str(tmp_path), accept_xml=True)[0] == path


def test_accept_xml_exits_3_with_nothing_on_stdout(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dp, "download_paper", lambda **k: (str(tmp_path / "a.jats.xml"), []))
    monkeypatch.setattr(dp, "load_key_file", lambda: {})
    monkeypatch.setattr(dp.sys, "argv", ["x", "--doi", SPRINGER_DOI, "--accept-xml"])
    monkeypatch.setattr(dp, "STDOUT", sys.stdout)

    with pytest.raises(SystemExit) as e:
        dp.main()

    assert e.value.code == 3
    assert capsys.readouterr().out == ""


def test_load_key_file_reads_comments_and_values(monkeypatch, tmp_path):
    keys = tmp_path / "keys.env"
    keys.write_text("# Publisher APIs\nWILEY_TDM_TOKEN=abc\nexport SPRINGER_OA_API_KEY='def'\n")
    monkeypatch.setattr(dp, "KEY_FILE", str(keys))
    monkeypatch.delenv("WILEY_TDM_TOKEN", raising=False)
    monkeypatch.delenv("SPRINGER_OA_API_KEY", raising=False)

    loaded = dp.load_key_file()

    assert loaded["WILEY_TDM_TOKEN"] == "abc" and loaded["SPRINGER_OA_API_KEY"] == "def"


# --- The Chrome worker window, with osascript mocked -------------------------

CHROME_MAIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture
def osa(monkeypatch, tmp_path):
    """Script osascript's answers; record scripts; never touch a real Chrome."""
    state = {"scripts": [], "answers": lambda script: ("", "")}

    def fake_osa(script, timeout=90):
        state["scripts"].append(script)
        return state["answers"](script)

    monkeypatch.setattr(dp.chrome, "_osa", fake_osa)
    monkeypatch.setattr(dp.chrome, "WORKER_LOCK", tmp_path / "chrome_worker.lock")
    monkeypatch.setattr(dp.chrome.time, "sleep", lambda s: None)
    return state


def made_stamp(tmp_path, age=0):
    import time
    (tmp_path / "chrome_worker.made").write_text(str(time.time() - age))


def windows_made(osa):
    return [s for s in osa["scripts"] if "make new window" in s]


def test_worker_window_is_reused(osa, tmp_path):
    osa["answers"] = lambda s: ("42", "") if "repeat with w" in s else ("", "")
    assert dp.chrome.worker_window() == "42"
    assert not windows_made(osa)


def test_a_recent_stamp_blocks_a_second_window(osa, tmp_path):
    made_stamp(tmp_path)
    osa["answers"] = lambda s: ("3", "") if "count of windows" in s else ("", "")

    with pytest.raises(dp.chrome.ChromeError, match="last hour"):
        dp.chrome.worker_window()

    assert not windows_made(osa)


def test_an_old_stamp_allows_one_new_window(osa, tmp_path):
    made_stamp(tmp_path, age=7200)
    made = []

    def answers(s):
        if "make new window" in s:
            made.append(s)
        if "repeat with w" in s:
            return ("7", "") if made else ("", "")
        return ("3", "") if "count of windows" in s else ("", "")

    osa["answers"] = answers

    assert dp.chrome.worker_window() == "7"
    assert len(windows_made(osa)) == 1
    assert any("set minimized of window id 7 to true" in s for s in osa["scripts"])


def test_no_window_is_made_when_chrome_has_none(osa, tmp_path):
    osa["answers"] = lambda s: ("0", "") if "count of windows" in s else ("", "")
    with pytest.raises(dp.chrome.ChromeError, match="no window"):
        dp.chrome.worker_window()
    assert not windows_made(osa)


def test_work_tab_is_reused(osa, tmp_path):
    def answers(s):
        if "repeat with w" in s:
            return ("42", "")
        if "count of tabs" in s:
            return ("2", "")
        return ("99", "") if "id of tab 2" in s else ("OK", "")

    osa["answers"] = answers

    with dp.chrome.ChromeTab() as tab:
        assert tab.id == "99"
    assert not [s for s in osa["scripts"] if "make new tab" in s]


def test_lost_tab_is_retried_then_raises(osa):
    osa["answers"] = lambda s: ("NOTAB", "")
    tab = dp.chrome.ChromeTab()
    tab.id = "99"

    with pytest.raises(dp.chrome.ChromeError, match="work tab"):
        tab.js("1+1")

    assert len(osa["scripts"]) == 8


def test_tab_is_addressed_by_id(osa):
    osa["answers"] = lambda s: ("2", "")
    tab = dp.chrome.ChromeTab()
    tab.id = "99"
    assert tab.js("1+1") == "2"
    assert "tab id 99 of w" in osa["scripts"][0]


def test_nav_stays_inside_the_page(osa):
    osa["answers"] = lambda s: ("1", "")
    tab = dp.chrome.ChromeTab()
    tab.id = "99"
    tab.nav("https://a.org/x'y")
    assert "location.href" in osa["scripts"][0] and "a.org/x\\\\'y" in osa["scripts"][0]
    assert not [s for s in osa["scripts"] if "set URL" in s]


def test_nav_leaves_an_error_page_with_set_url_and_reminimises(osa):
    osa["answers"] = lambda s: (("", "JS error") if "location.href" in s
                                else ("42", "") if "get URL of every tab" in s else ("OK", ""))
    tab = dp.chrome.ChromeTab()
    tab.id = "99"
    tab.nav("https://a.org/x")
    assert any("set URL of tt" in s for s in osa["scripts"])
    assert any("set minimized of window id 42" in s for s in osa["scripts"])


@pytest.mark.parametrize("err, allowed", [
    ("Executing JavaScript through AppleScript is turned off.", False),
])
def test_javascript_refused_only_on_turned_off(osa, err, allowed):
    osa["answers"] = lambda s: ("", err)
    tab = dp.chrome.ChromeTab()
    tab.id = "99"
    assert tab.javascript_allowed() is allowed


def test_other_javascript_errors_raise(osa):
    osa["answers"] = lambda s: ("", "Chrome got an error: busy")
    tab = dp.chrome.ChromeTab()
    tab.id = "99"
    with pytest.raises(dp.chrome.ChromeError, match="busy"):
        tab.javascript_allowed()


def test_extra_chrome_instances_ignores_the_users_chrome_and_helpers(monkeypatch):
    ps = (f"  100 {CHROME_MAIN}\n"
          f"  200 {CHROME_MAIN} --user-data-dir=/tmp/pw --remote-debugging-pipe\n"
          "  300 /Applications/Google Chrome.app/Contents/Frameworks/Helper --type=renderer\n"
          "  400 /Users/x/Library/Caches/ms-playwright/chromium/Chromium --no-sandbox\n")
    monkeypatch.setattr(dp.chrome.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": ps})())
    assert dp.chrome.extra_chrome_instances() == ["200"]


def test_wait_for_one_chrome_gives_up(monkeypatch):
    monkeypatch.setattr(dp.chrome, "extra_chrome_instances", lambda: ["200"])
    monkeypatch.setattr(dp.chrome.time, "sleep", lambda s: None)
    with pytest.raises(dp.chrome.ChromeError, match="another Google Chrome"):
        dp.chrome.wait_for_one_chrome(timeout=-1)
