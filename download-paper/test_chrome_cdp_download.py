"""chrome_cdp_download's identity, output and cap logic, with Chrome mocked.

Run with:
    cd download-paper && uv run --no-project --with pytest --with requests \
      pytest test_chrome_cdp_download.py
"""

import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chrome_cdp_download as cdp

DOI = "10.1007/abc"
PDF = b"%PDF-" + b"x" * 30_000
RECORD = {"title": "A Real Title", "pages": "1-9"}


class Page:
    url = "https://link.springer.com/article/x"

    def set_default_timeout(self, *a): pass
    def goto(self, *a, **k): pass
    def close(self): pass


class Ctx:
    def new_page(self):
        return Page()


def make_args(out, **over):
    values = dict(out=str(out), no_verify=False, institution="bbk.ac.uk", springer_wayf=False,
                  institution_name="Birkbeck College", title="", doi=[], doi_file=None,
                  max_fetches=30, chrome=None, profile_root=None, profile="Default",
                  keep_profile=False)
    return types.SimpleNamespace(**{**values, **over})


@pytest.fixture
def walk(monkeypatch):
    """A page walk with one candidate URL; `verdicts` scripts the identity check."""
    verdicts = {"ok": True}
    monkeypatch.setattr(cdp, "settle", lambda *a, **k: None)
    monkeypatch.setattr(cdp, "unlock", lambda *a, **k: None)
    monkeypatch.setattr(cdp, "pdf_links", lambda page: [])
    monkeypatch.setattr(cdp, "templates", lambda doi: ["https://pub/x.pdf"])
    monkeypatch.setattr(cdp, "capture_pdf", lambda page, url: PDF)
    monkeypatch.setattr(cdp.dp, "verify_identity",
                        lambda *a: types.SimpleNamespace(ok=verdicts["ok"], state="s", reason="r"))
    return verdicts


def test_a_verified_pdf_is_saved_under_the_doi(walk, tmp_path):
    assert cdp.attempt(DOI, Ctx(), make_args(tmp_path), RECORD, lambda m: None)
    assert (tmp_path / "10.1007_abc.pdf").read_bytes() == PDF
    assert not list(tmp_path.glob("*.part"))


def test_a_refused_pdf_leaves_nothing(walk, tmp_path):
    walk["ok"] = False
    assert not cdp.attempt(DOI, Ctx(), make_args(tmp_path), RECORD, lambda m: None)
    assert not list(tmp_path.iterdir())


def test_a_redirect_during_evaluate_is_retried():
    answers = [RuntimeError("Page.evaluate: Execution context was destroyed"), "ok"]

    class Redirecting(Page):
        def wait_for_timeout(self, *a): pass

        def evaluate(self, script):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    assert cdp.evaluate(Redirecting(), "x") == "ok"


def test_one_page_error_does_not_end_the_batch(monkeypatch, tmp_path):
    def boom(*a):
        raise RuntimeError("Page.goto: Timeout\nlong trace")

    monkeypatch.setattr(cdp, "attempt", boom)
    logged = []
    assert not cdp.fetch_one(DOI, Ctx(), make_args(tmp_path), RECORD, logged.append)
    assert "Page.goto: Timeout" in logged[0] and "long trace" not in logged[0]


def test_no_verify_skips_the_identity_check(walk, tmp_path):
    walk["ok"] = False
    assert cdp.attempt(DOI, Ctx(), make_args(tmp_path, no_verify=True),
                       {"title": None, "pages": None}, lambda m: None)
    assert (tmp_path / "10.1007_abc.pdf").exists()


def springer_after_wayf(monkeypatch):
    """The publisher serves its PDF only once the WAYF picker has run."""
    signed_in = []
    monkeypatch.setattr(cdp, "capture_pdf", lambda page, url: PDF if signed_in else None)
    monkeypatch.setattr(cdp, "springer_wayf", lambda *a: signed_in.append(1) or True)


def test_springer_wayf_saves_the_file_it_reports(walk, monkeypatch, tmp_path):
    springer_after_wayf(monkeypatch)
    args = make_args(tmp_path, springer_wayf=True)
    assert cdp.attempt(DOI, Ctx(), args, RECORD, lambda m: None)
    assert (tmp_path / "10.1007_abc.pdf").exists()


def test_springer_wayf_output_is_identity_checked(walk, monkeypatch, tmp_path):
    springer_after_wayf(monkeypatch)
    walk["ok"] = False
    args = make_args(tmp_path, springer_wayf=True)
    assert not cdp.attempt(DOI, Ctx(), args, RECORD, lambda m: None)
    assert not list(tmp_path.iterdir())


def test_institution_name_comes_from_the_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("INSTITUTION_NAME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cdp.Path, "home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "api_keys.env").write_text("export INSTITUTION_NAME='Birkbeck College'\n")
    assert cdp.env_value("INSTITUTION_NAME") == "Birkbeck College"


# --- plan: nothing here may touch a publisher --------------------------------

def crossref(monkeypatch, title="A Real Title"):
    monkeypatch.setattr(cdp.dp, "crossref_metadata",
                        lambda doi: {"title": title, "pages": "1-9"} if title else {})


def test_a_doi_without_a_title_is_not_fetched(monkeypatch, tmp_path):
    crossref(monkeypatch, title=None)
    assert cdp.plan([DOI], make_args(tmp_path)) == ([], [DOI], [])


def test_title_argument_is_the_fallback(monkeypatch, tmp_path):
    crossref(monkeypatch, title=None)
    _, _, pending = cdp.plan([DOI], make_args(tmp_path, title="Given"))
    assert pending[0][1]["title"] == "Given"


def test_a_saved_file_is_rechecked_not_trusted(monkeypatch, tmp_path):
    crossref(monkeypatch)
    (tmp_path / "10.1007_abc.pdf").write_bytes(PDF)
    monkeypatch.setattr(cdp.dp, "verify_identity", lambda *a: types.SimpleNamespace(ok=False))
    present, _, pending = cdp.plan([DOI], make_args(tmp_path))
    assert not present and pending[0][0] == DOI
    monkeypatch.setattr(cdp.dp, "verify_identity", lambda *a: types.SimpleNamespace(ok=True))
    present, _, pending = cdp.plan([DOI], make_args(tmp_path))
    assert present and not pending


# --- main: the cap and the argument checks -----------------------------------

def run_main(monkeypatch, argv):
    fetched = []
    monkeypatch.setattr(cdp, "find_browser", lambda args: ("chrome", "root"))
    monkeypatch.setattr(cdp, "fetch_all", lambda pending, args, browser: fetched.extend(pending) or 0)
    monkeypatch.setattr(sys, "argv", ["chrome_cdp_download.py", *argv])
    with pytest.raises(SystemExit) as stop:
        cdp.main()
    return stop.value.code, fetched


def doi_file(tmp_path, n):
    path = tmp_path / "dois.txt"
    path.write_text("\n".join(f"10.1000/p{i}" for i in range(n)))
    return str(path)


def test_batches_stop_at_30_by_default(monkeypatch, tmp_path, capsys):
    crossref(monkeypatch)
    code, fetched = run_main(monkeypatch, ["--doi-file", doi_file(tmp_path, 31), "--out", str(tmp_path)])
    assert len(fetched) == 30 and fetched[0][0] == "10.1000/p0" and code == 1
    assert "1 DOIs not attempted" in capsys.readouterr().err


def test_exactly_30_is_within_the_cap(monkeypatch, tmp_path):
    crossref(monkeypatch)
    code, fetched = run_main(monkeypatch, ["--doi-file", doi_file(tmp_path, 30), "--out", str(tmp_path)])
    assert len(fetched) == 30 and code == 0


def test_saved_files_do_not_count_towards_the_cap(monkeypatch, tmp_path):
    crossref(monkeypatch)
    monkeypatch.setattr(cdp.dp, "verify_identity", lambda *a: types.SimpleNamespace(ok=True))
    for i in range(5):
        (tmp_path / f"10.1000_p{i}.pdf").write_bytes(PDF)
    code, fetched = run_main(monkeypatch, ["--doi-file", doi_file(tmp_path, 35), "--out", str(tmp_path)])
    assert len(fetched) == 30 and code == 0


def test_title_is_refused_with_several_dois(monkeypatch, tmp_path):
    code, fetched = run_main(monkeypatch, ["--doi", "10.1000/a", "--doi", "10.1000/b", "--title", "T",
                                           "--out", str(tmp_path)])
    assert "one DOI" in str(code) and not fetched


def test_springer_wayf_needs_a_name(monkeypatch, tmp_path):
    monkeypatch.setattr(cdp, "env_value", lambda name, default="": default)
    code, fetched = run_main(monkeypatch, ["--doi", DOI, "--springer-wayf", "--out", str(tmp_path)])
    assert "institution-name" in str(code) and not fetched


# --- whether this machine can run the route ----------------------------------

@pytest.fixture
def linux(monkeypatch, tmp_path):
    """A Linux desktop with no browser; `which` finds only what a test adds."""
    found = {}
    monkeypatch.setattr(cdp.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(cdp.shutil, "which", lambda name: found.get(name))
    monkeypatch.setattr(cdp, "BROWSERS", {"linux": [("google-chrome", tmp_path / "gc"),
                                                    ("chromium", tmp_path / "cr")]})
    return found


def test_no_browser_means_unavailable(linux, tmp_path):
    assert cdp.find_browser(make_args(tmp_path)) is None


def test_a_browser_without_a_profile_is_passed_over(linux, tmp_path):
    linux.update({"google-chrome": "/bin/gc", "chromium": "/bin/cr"})
    (tmp_path / "cr" / "Default").mkdir(parents=True)
    assert cdp.find_browser(make_args(tmp_path)) == ("/bin/cr", tmp_path / "cr")


def test_no_display_means_unavailable(linux, monkeypatch, tmp_path):
    linux["google-chrome"] = "/bin/gc"
    (tmp_path / "gc" / "Default").mkdir(parents=True)
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert cdp.find_browser(make_args(tmp_path)) is None


def test_explicit_paths_work_on_an_unlisted_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(cdp.sys, "platform", "freebsd")
    monkeypatch.setattr(cdp.shutil, "which", lambda name: name)
    (tmp_path / "Default").mkdir()
    args = make_args(tmp_path, chrome="/opt/chrome", profile_root=str(tmp_path))
    assert cdp.find_browser(args) == ("/opt/chrome", tmp_path)


def test_unavailable_exits_2_and_saved_files_need_no_browser(monkeypatch, tmp_path):
    crossref(monkeypatch)
    monkeypatch.setattr(cdp, "find_browser", lambda args: None)
    monkeypatch.setattr(sys, "argv", ["x", "--doi", DOI, "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as stop:
        cdp.main()
    assert stop.value.code == 2

    monkeypatch.setattr(cdp.dp, "verify_identity", lambda *a: types.SimpleNamespace(ok=True))
    (tmp_path / "10.1007_abc.pdf").write_bytes(PDF)
    with pytest.raises(SystemExit) as stop:
        cdp.main()
    assert stop.value.code == 0


# --- reporting ---------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://login.openathens.net/auth",
                                 "https://login.microsoftonline.com/x/saml2",
                                 "https://idp.bbk.ac.uk/sso"])
def test_a_sign_in_page_is_reported_as_a_session_problem(url):
    assert "session" in cdp.failure_reason(url, "bbk.ac.uk")


def test_a_publisher_page_is_reported_as_entitlement():
    assert "entitlement" in cdp.failure_reason("https://www.emerald.com/a/abstract", "bbk.ac.uk")


def test_a_target_quoted_in_the_query_is_not_a_login():
    assert not cdp.at_login("https://pub.example/article?return=https://login.example/")


def test_fetchpdf_pin_matches_download_paper():
    here = os.path.dirname(os.path.abspath(__file__))
    pins = [re.search(r"fetchpdf @ \S+?@(\w+)", open(os.path.join(here, name)).read()).group(1)
            for name in ("download_paper.py", "chrome_cdp_download.py")]
    assert pins[0] == pins[1]
