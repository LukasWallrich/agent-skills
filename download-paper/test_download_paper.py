"""The Google Scholar and Chrome candidate loops, with network and Chrome mocked.

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
    accepted, blocked, wrong = dp.scholar_download(
        [BLOCKED_URL, WRONG_URL, RIGHT_URL], tmp, "10.1/x", "A Title", "1-9")

    assert accepted == RIGHT_URL
    assert blocked == [BLOCKED_URL]
    assert wrong == [WRONG_URL]
    assert scholar == [BLOCKED_URL, WRONG_URL, RIGHT_URL]
    assert dp.validate_pdf(tmp)


def test_no_right_paper_leaves_nothing_behind(scholar, tmp_path):
    tmp = str(tmp_path / "part.pdf")
    accepted, blocked, wrong = dp.scholar_download(
        [WRONG_URL, BLOCKED_URL], tmp, "10.1/x", "A Title", "1-9")

    assert accepted is None
    assert blocked == [BLOCKED_URL]
    assert wrong == [WRONG_URL]
    assert not os.path.exists(tmp)


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
