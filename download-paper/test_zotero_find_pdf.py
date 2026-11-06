"""Find Available PDF: the Python caller, the plugin's own tests, and its package."""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import download_paper as dp
import zotero_local as zl

PLUGIN = Path(__file__).parent / "zotero-plugin"
DOI = "10.1234/abc"
GOOD_PDF = b"%PDF-1.4 the paper"


class Response:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self.text = text if text is not None else json.dumps(body)
        self.headers = {"Content-Type": "text/plain" if text is not None else "application/json"}
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)


class Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(tmp_path, *answers, keys=None):
    zot = zl.ZoteroLocal(key_file=str(tmp_path / "key.json"))
    zot.server_id = "SERVER"
    zot.session = Session(*answers)
    (tmp_path / "key.json").write_text(json.dumps(keys if keys is not None else {"SERVER": "KEY"}))
    return zot


# ---------------- the request ----------------
def test_asks_for_one_item_and_names_nothing_else(tmp_path):
    zot = client(tmp_path, Response(body={"state": "none"}))
    assert zot.find_available_pdf("ABCD2345")["state"] == "none"
    url, kwargs = zot.session.posts[0]
    assert url == "http://127.0.0.1:23119/find-pdf-api/v1/find"
    assert kwargs["json"] == {"itemKey": "ABCD2345"}
    assert kwargs["headers"] == {"Zotero-Server-ID": "SERVER", "Zotero-API-Key": "KEY"}
    assert kwargs["timeout"] > 240


# ---------------- result states ----------------
@pytest.mark.parametrize("answer,state", [
    (Response(404, text="No endpoint found\n"), "not-installed"),
    (requests.ConnectionError(), "zotero-closed"),
    (requests.Timeout(), "timeout"),
    (Response(body={"state": "found", "attachmentKey": "ATT"}), "found"),
    (Response(body={"state": "none"}), "none"),
    (Response(body={"state": "not-eligible"}), "not-eligible"),
    (Response(409, body={"state": "busy"}), "busy"),
    (Response(403, body={"state": "local-api-off"}), "local-api-off"),
    (Response(501, body={"state": "unsupported"}), "unsupported"),
    (Response(500, text="An error occurred"), "error"),
    (Response(200, body={"state": "something new"}), "error"),
])
def test_result_states(tmp_path, answer, state):
    assert client(tmp_path, answer).find_available_pdf("A")["state"] == state


def test_a_refused_key_is_requested_once_more(tmp_path, monkeypatch):
    zot = client(tmp_path, Response(401, text="no"), Response(body={"state": "none"}))
    asked = []
    monkeypatch.setattr(zot, "_authorize", lambda: asked.append(1) or "NEW")
    assert zot.find_available_pdf("A")["state"] == "none"
    assert asked == [1] and zot.session.posts[1][1]["headers"]["Zotero-API-Key"] == "NEW"


def test_no_key_and_no_plugin_never_opens_a_dialog(tmp_path, monkeypatch):
    zot = client(tmp_path, Response(404, text="No endpoint found\n"), keys={})
    monkeypatch.setattr(zot, "_authorize", lambda: pytest.fail("asked for a key"))
    assert zot.find_available_pdf("A")["state"] == "not-installed"
    assert "Zotero-API-Key" not in zot.session.posts[0][1]["headers"]


def test_a_key_refused_twice_is_not_authorised(tmp_path, monkeypatch):
    zot = client(tmp_path, Response(401, text="no"), Response(401, text="no"))
    monkeypatch.setattr(zot, "_authorize", lambda: None)
    assert zot.find_available_pdf("A")["state"] == "not-authorised"


def test_a_refused_dialog_is_not_authorised(tmp_path):
    zot = client(tmp_path, Response(401, text="no key"), Response(403, text="denied"), keys={})
    assert zot.find_available_pdf("A")["state"] == "not-authorised"


def test_a_new_key_keeps_the_keys_of_other_zotero_instances(tmp_path):
    zot = client(tmp_path, Response(body={"key": "NEW", "remember": True}), keys={"OTHER": "KEPT"})
    assert zot._authorize() == "NEW"
    assert json.loads((tmp_path / "key.json").read_text()) == {"OTHER": "KEPT", "SERVER": "NEW"}


# ---------------- verification and removal ----------------
class FakeZotero:
    def __init__(self, result, file_path=None):
        self.result = result
        self.path = file_path
        self.deleted = []
        self.untagged = []

    def find_available_pdf(self, key):
        return self.result

    def file_path(self, key):
        return self.path

    def delete_item(self, key):
        self.deleted.append(key)
        return True

    def remove_tag(self, key, tag):
        self.untagged.append((key, tag))


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Runs zotero_find_pdf with a stated verdict, recording what reaches the shared cache."""
    source = tmp_path / "zotero-storage.pdf"
    source.write_bytes(GOOD_PDF)
    shared = []
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {"title": "A Paper", "pages": None})
    monkeypatch.setattr(dp, "validate_pdf", lambda path: True)
    monkeypatch.setattr(dp, "share_pdf", lambda path, doi, route, overwrite=False: shared.append(route))

    def go(result, ok=True, file_path=source):
        zot = FakeZotero(result, str(file_path) if file_path else None)
        monkeypatch.setitem(dp._shared, "zotero", zot)
        monkeypatch.setattr(dp, "verify_identity", lambda *a: SimpleNamespace(ok=ok, state="wrong", reason="no"))
        cache = tmp_path / "cache"
        path = dp.zotero_find_pdf("ITEM", DOI, cache_dir=str(cache))
        return SimpleNamespace(path=path, zot=zot, shared=shared, cache=cache)

    return go


FOUND = {"state": "found", "attachmentKey": "ATT", "contentType": "application/pdf",
         "itemTagged": True, "methodsTried": ["doi", "oa"]}


def test_a_verified_pdf_is_cached_shared_and_loses_needs_pdf(run, capsys):
    done = run(FOUND)
    assert Path(done.path).read_bytes() == GOOD_PDF
    assert done.shared == ["zotero-find-pdf"]
    assert done.zot.deleted == [] and done.zot.untagged == [("ITEM", "needs-pdf")]
    assert "ZOTERO_FIND_PDF=found key=ITEM attachment=ATT via=oa" in capsys.readouterr().err


def test_a_failed_check_removes_the_attachment_and_caches_nothing(run, capsys):
    done = run(FOUND, ok=False)
    assert done.path is None and done.shared == []
    assert done.zot.deleted == ["ATT"]
    assert done.zot.untagged == [("ITEM", "agent-added")]
    assert not list(done.cache.glob("*"))
    assert "ZOTERO_FIND_PDF=rejected key=ITEM attachment=ATT via=oa removed=yes" in capsys.readouterr().err


def test_a_tag_the_item_already_had_stays_on_rejection(run):
    assert run({**FOUND, "itemTagged": False}, ok=False).zot.untagged == []


def test_a_failed_removal_keeps_the_agent_tag_for_manual_cleanup(run, monkeypatch, capsys):
    monkeypatch.setattr(FakeZotero, "delete_item", lambda *a: False)
    done = run(FOUND, ok=False)
    assert done.zot.untagged == []
    assert "removed=no" in capsys.readouterr().err


def test_a_non_pdf_is_removed_unchecked(run, capsys):
    done = run({**FOUND, "contentType": "application/epub+zip"})
    assert done.path is None and done.zot.deleted == ["ATT"] and done.shared == []
    assert "not a PDF" in capsys.readouterr().err


def test_a_file_not_on_disk_is_removed(run):
    done = run(FOUND, file_path=None)
    assert done.path is None and done.zot.deleted == ["ATT"]


def test_a_crash_in_the_check_still_removes_the_attachment(run, monkeypatch):
    def boom(*a):
        raise RuntimeError("no parser")
    monkeypatch.setattr(dp, "accept_bytes", boom)
    done = run(FOUND)
    assert done.path is None and done.zot.deleted == ["ATT"] and done.shared == []


# ---------------- degrading ----------------
@pytest.mark.parametrize("state", ["not-installed", "none", "zotero-closed", "busy", "timeout"])
def test_other_states_log_one_line_and_touch_nothing(run, capsys, state):
    done = run({"state": state})
    assert done.path is None and done.zot.deleted == [] and done.shared == []
    assert f"ZOTERO_FIND_PDF={state}" in capsys.readouterr().err


def test_a_machine_without_zotero_only_logs(monkeypatch, capsys):
    monkeypatch.setitem(dp._shared, "zotero", None)
    assert dp.zotero_find_pdf(None, DOI) is None
    assert "ZOTERO_FIND_PDF=zotero-closed" in capsys.readouterr().err


def run_main(monkeypatch, capsys, path, *flags):
    """main() with the download stubbed, as (exit code, stdout, stderr)."""
    written = []
    monkeypatch.setattr(sys, "argv", ["download_paper.py", "--doi", DOI, *flags])
    monkeypatch.setattr(dp, "download_paper", lambda **kw: (path, []))
    monkeypatch.setattr(dp, "load_key_file", lambda: {})
    monkeypatch.setattr(dp, "STDOUT", sys.stdout)
    with pytest.raises(SystemExit) as stop:
        dp.main()
    out, err = capsys.readouterr()
    return stop.value.code, out, err


def test_flag_without_zotero_keeps_the_exit_code_and_a_clean_stdout(monkeypatch, capsys):
    monkeypatch.setitem(dp._shared, "zotero", None)
    code, out, err = run_main(monkeypatch, capsys, None, "--add-to-zotero", "--zotero-find-pdf")
    assert code == 1 and out == ""
    assert "ZOTERO=not-added" in err and "ZOTERO_FIND_PDF=zotero-closed" in err


def test_flag_alone_says_what_it_needs(monkeypatch, capsys):
    code, out, err = run_main(monkeypatch, capsys, "/tmp/p.pdf", "--zotero-find-pdf")
    assert code == 0 and out.strip() == os.path.abspath("/tmp/p.pdf")
    assert "ZOTERO_FIND_PDF=skipped" in err


def test_a_found_pdf_goes_to_stdout_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(dp, "add_to_zotero", lambda doi, path: "ITEM")
    monkeypatch.setattr(dp, "zotero_find_pdf", lambda *a: "/tmp/found.pdf")
    code, out, _ = run_main(monkeypatch, capsys, None, "--add-to-zotero", "--zotero-find-pdf")
    assert code == 0 and out.strip() == "/tmp/found.pdf"


def test_a_pdf_in_hand_does_not_trigger_zotero(monkeypatch, capsys):
    monkeypatch.setattr(dp, "add_to_zotero", lambda doi, path: "ITEM")
    monkeypatch.setattr(dp, "zotero_find_pdf", lambda *a: pytest.fail("should not run"))
    code, out, _ = run_main(monkeypatch, capsys, "/tmp/p.pdf", "--add-to-zotero", "--zotero-find-pdf")
    assert code == 0 and out.strip() == os.path.abspath("/tmp/p.pdf")


# ---------------- the plugin ----------------
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_plugin_request_handling():
    done = subprocess.run(["node", "--test", "test/handler.test.js"], cwd=PLUGIN,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_package_matches_the_source():
    done = subprocess.run([sys.executable, "build.py", "--check"], cwd=PLUGIN, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_package_is_installable_and_updates_to_itself():
    manifest = json.loads((PLUGIN / "src/manifest.json").read_text())
    version = manifest["version"]
    settings = manifest["applications"]["zotero"]
    with zipfile.ZipFile(PLUGIN / f"dist/find-pdf-api-{version}.xpi") as xpi:
        assert {"manifest.json", "bootstrap.js", "handler.js"} <= set(xpi.namelist())
    update = json.loads((PLUGIN / "update.json").read_text())["addons"][settings["id"]]["updates"][0]
    assert update["version"] == version
    assert update["update_link"].endswith(f"find-pdf-api-{version}.xpi")
    assert update["applications"]["zotero"]["strict_max_version"] == settings["strict_max_version"]


def test_nothing_shared_holds_private_details():
    private = [os.path.expanduser("~"), "lukas.wallrich@", "/Users/", "r2.cloudflarestorage", "zotero_local_key"]
    for path in PLUGIN.rglob("*"):
        if path.is_file() and path.suffix != ".xpi":
            text = path.read_text()
            assert not [word for word in private if word in text], path
