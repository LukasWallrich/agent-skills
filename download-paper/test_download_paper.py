"""The web-search, Google Scholar and Chrome candidate loops, with network and Chrome mocked.

Run with:
    cd /Users/lukaswallrich/Documents/Coding/agent-skills/download-paper && \
      /Users/lukaswallrich/.local/bin/uv run --no-project --with pytest --with requests \
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
