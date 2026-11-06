"""Regression coverage for cache failures, library reads, and resumable harvesting."""

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import chrome_cdp_download as cdp
import download_paper as dp
import harvest_pdfs as hp
import seed_shared_cache as seed
import zotero_local as zl
from r2_cache import R2Cache
from test_shared_stores import Body, FakeS3, Ok, Wrong, PDF, DOI
from test_zotero_find_pdf import Response, client


def test_r2_setup_failure_does_not_abort_a_download(monkeypatch, tmp_path):
    keys = dict.fromkeys(("PAPER_CACHE_R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"), "x")
    monkeypatch.setattr(R2Cache, "_from_keys", lambda *a: (_ for _ in ()).throw(ValueError("invalid config")))
    monkeypatch.setattr(dp, "load_key_file", lambda: keys)
    monkeypatch.setattr(dp, "_shared", {"zotero": None})
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {})
    def fetched(doi, path, *a):
        Path(path).write_bytes(PDF)
        return True
    monkeypatch.setattr(dp, "fetchpdf_download", fetched)
    path, _ = dp.download_paper(DOI, cache_dir=str(tmp_path))
    assert Path(path).read_bytes() == PDF


def test_r2_fetch_cannot_create_temp_file_but_leaves_download_usable(tmp_path):
    cache = R2Cache(FakeS3(), "bucket")
    assert cache.fetch("paper.pdf", str(tmp_path / "missing" / "paper.pdf")) is None
    assert cache.off


def test_r2_stream_is_closed_even_on_a_failed_download(tmp_path):
    class BrokenBody(Body):
        closed = False
        def iter_chunks(self, size):
            yield PDF
            raise OSError("stream failed")
        def close(self):
            self.closed = True
    body = BrokenBody(PDF)
    cache = R2Cache(SimpleNamespace(get_object=lambda **kw: {"Body": body}), "bucket")
    dest = tmp_path / "paper.pdf"
    dest.write_bytes(b"previous copy")
    assert cache.fetch("paper.pdf", str(dest)) is None
    assert dest.read_bytes() == b"previous copy" and body.closed
    assert not list(tmp_path.glob("part-r2-*"))


def test_r2_does_not_overwrite_a_concurrent_upload(tmp_path):
    class PreconditionFailed(Exception):
        response = {"Error": {"Code": "PreconditionFailed"}}
    class Concurrent(FakeS3):
        def put_object(self, **kw):
            assert kw["IfNoneMatch"] == "*"
            self.objects[kw["Key"]] = (b"other verified copy", {})
            raise PreconditionFailed()
    cache = R2Cache(Concurrent(), "bucket")
    path = tmp_path / "paper.pdf"
    path.write_bytes(PDF)
    assert cache.store(path, "paper.pdf") and not cache.off
    assert cache.client.objects["paper-cache/paper.pdf"][0] == b"other verified copy"


def test_seeding_a_legacy_name_preserves_both_local_copies(tmp_path, monkeypatch):
    old = tmp_path / (seed.md5(DOI) + ".pdf")
    current = tmp_path / (seed.md5(DOI.lower()) + ".pdf")
    old.write_bytes(PDF)
    current.write_bytes(b"a different existing file")
    monkeypatch.setattr(seed, "recover_doi", lambda *a: DOI)
    r2 = R2Cache(FakeS3(), "bucket")
    assert seed.seed_one(old.name, str(tmp_path), r2) == (True, True)
    assert old.read_bytes() == PDF and current.read_bytes() == b"a different existing file"
    assert r2.client.objects["paper-cache/" + current.name][0] == PDF


def test_zotero_doi_match_on_second_page_is_not_missed(monkeypatch):
    zot = zl.ZoteroLocal()
    citing = [{"key": str(i), "data": {"itemType": "journalArticle", "DOI": "10.1/other"}} for i in range(100)]
    wanted = {"key": "TARGET", "data": {"itemType": "journalArticle", "DOI": DOI}}
    calls = []
    def get(path, **kw):
        calls.append(kw["start"])
        return SimpleNamespace(json=lambda: citing if kw["start"] == 0 else [wanted])
    monkeypatch.setattr(zot, "_get", get)
    assert zot.find(DOI) == [wanted] and calls == [0, 100]


def test_personal_library_pdf_does_not_wait_for_a_broken_group_lookup(monkeypatch, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(PDF)
    zot = zl.ZoteroLocal()
    monkeypatch.setattr(zot, "find", lambda *a: [{"key": "TARGET"}])
    monkeypatch.setattr(zot, "pdfs", lambda *a: [str(path)])
    monkeypatch.setattr(zot, "_all", lambda *a, **kw: pytest.fail("group lookup ran"))
    monkeypatch.setitem(dp._shared, "zotero", zot)
    monkeypatch.setattr(dp, "accept_bytes", lambda data, *a: data == PDF)
    assert dp.find_existing(DOI, "Title", None, str(tmp_path / "out.pdf")) == "zotero"


def test_titles_preserve_non_latin_words():
    assert zl.normal_title("研究结果 2024") != zl.normal_title("不同文章 2024")
    assert zl.normal_title("Éducation: 研究") == zl.normal_title("éducation 研究")


def test_authorization_creates_private_storage_on_a_fresh_machine(tmp_path):
    zot = zl.ZoteroLocal(key_file=str(tmp_path / "new" / "key.json"))
    zot.server_id = "SERVER"
    zot.session = SimpleNamespace(post=lambda *a, **kw: Response(body={"key": "NEW", "remember": True}))
    assert zot._authorize() == "NEW"
    assert zot._saved_key() == "NEW"
    assert Path(zot.key_file).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body", [[], {"state": []}, {"state": None}])
def test_bad_saved_key_file_and_malformed_plugin_reply_are_handled(tmp_path, body):
    zot = client(tmp_path, Response(body=body), keys=[])
    assert zot._saved_key() == ""
    assert zot.find_available_pdf("ABCD2345")["state"] == "error"


def test_key_storage_failure_is_a_result_not_a_crash(tmp_path, monkeypatch):
    zot = client(tmp_path, Response(401, text="missing"), keys={})
    monkeypatch.setattr(zot, "_authorize", lambda: (_ for _ in ()).throw(OSError("read only")))
    assert zot.find_available_pdf("ABCD2345")["state"] == "not-authorised"


def test_missing_source_pdf_does_not_create_an_empty_attachment(tmp_path, monkeypatch):
    zot = zl.ZoteroLocal()
    monkeypatch.setattr(zot, "_create", lambda *a: pytest.fail("created an attachment"))
    with pytest.raises(FileNotFoundError):
        zot.attach("ABCD2345", tmp_path / "absent.pdf", "paper.pdf")


def test_failed_upload_does_not_expose_its_temporary_credential(tmp_path, monkeypatch):
    zot = zl.ZoteroLocal()
    source = tmp_path / "paper.pdf"
    source.write_bytes(PDF)
    removed = []
    monkeypatch.setattr(zot, "_create", lambda *a: "ATTA2345")
    monkeypatch.setattr(zot, "_write", lambda *a, **kw: Response(body={"url": "http://localhost/upload?token=PRIVATE", "uploadKey": "PRIVATE"}))
    monkeypatch.setattr(zot, "delete_item", lambda key: removed.append(key))
    def post(*a, **kw):
        raise dp.requests.ConnectionError("could not reach upload?token=PRIVATE")
    zot.session = SimpleNamespace(post=post)
    with pytest.raises(zl.ZoteroError) as error:
        zot.attach("ABCD2345", source, "paper.pdf")
    assert "PRIVATE" not in str(error.value) and removed == ["ATTA2345"]


def test_local_cache_copy_failure_does_not_discard_a_cdp_download(tmp_path, monkeypatch):
    out = tmp_path / "downloaded.pdf"
    out.write_bytes(PDF)
    monkeypatch.setattr(cdp.shutil, "copyfile", lambda *a: (_ for _ in ()).throw(OSError("cache full")))
    cdp.keep_shared(DOI, out, "chrome-cdp")
    assert out.read_bytes() == PDF


@pytest.mark.parametrize("ok", [True, False])
def test_institutional_output_is_replaced_only_after_verification(tmp_path, monkeypatch, ok):
    import institutional_fetch as inst
    out = tmp_path / "existing.pdf"
    out.write_bytes(b"original PDF")
    def verify(path, *a):
        assert out.read_bytes() == b"original PDF"
        assert Path(path).read_bytes() == PDF
        return Ok if ok else Wrong
    monkeypatch.setattr(inst, "_identity", lambda: (lambda doi: {}, verify))
    assert inst.save_verified(PDF, DOI, out) == ok
    assert out.read_bytes() == (PDF if ok else b"original PDF")
    assert not list(tmp_path.glob("part-*"))


def test_shared_xml_with_a_different_doi_does_not_stop_the_pdf_search(tmp_path, monkeypatch):
    r2 = R2Cache(FakeS3(), "bucket")
    name = os.path.basename(dp.doi_to_xml_path(DOI, ""))
    r2.client.objects["paper-cache/" + name] = (b'<article><front><article-meta><article-id pub-id-type="doi">10.9/other</article-id></article-meta></front></article>', {})
    monkeypatch.setitem(dp._shared, "r2", r2)
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {})
    def fetched(doi, path, *a):
        Path(path).write_bytes(PDF)
        return True
    monkeypatch.setattr(dp, "fetchpdf_download", fetched)
    path, _ = dp.download_paper(DOI, cache_dir=str(tmp_path), accept_xml=True)
    assert path.endswith(".pdf") and Path(path).read_bytes() == PDF
    assert not (tmp_path / name).exists()


@pytest.fixture
def harvest(monkeypatch, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(PDF)
    r2 = R2Cache(FakeS3(), "bucket")
    monkeypatch.setattr(hp, "candidates", lambda *a: ["10.1/cited", "10.1/correct"])
    monkeypatch.setattr(hp, "title_record", lambda doi: {"title": doi})
    monkeypatch.setattr(dp, "verify_identity", lambda path, doi, *a: Ok if doi.endswith("correct") else Wrong)
    return path, r2, set(), set(), threading.Lock()


def test_harvest_does_not_skip_a_paper_because_a_cited_doi_is_cached(harvest):
    path, r2, present, seen, lock = harvest
    present.add(os.path.basename(dp.doi_to_cache_path("10.1/cited", "")))
    result = hp.harvest_one(str(path), r2, present, seen, lock, False, "test")
    assert result["status"] == "uploaded" and result["doi"] == "10.1/correct"


def test_failed_harvest_upload_is_retried_for_identical_content(harvest, monkeypatch):
    path, r2, present, seen, lock = harvest
    monkeypatch.setattr(r2, "store", lambda *a, **kw: False)
    assert hp.harvest_one(str(path), r2, present, seen, lock, False, "test")["status"] == "upload-failed"
    assert not present and not seen
    monkeypatch.setattr(r2, "store", lambda *a, **kw: True)
    assert hp.harvest_one(str(path), r2, present, seen, lock, False, "test")["status"] == "uploaded"


def test_harvest_resume_rechecks_changed_failed_and_partial_records(tmp_path):
    paths = [tmp_path / str(i) for i in range(3)]
    for p in paths:
        p.write_bytes(PDF)
    records = [{"path": str(p), "stamp": hp.file_stamp(p), "status": "uploaded"} for p in paths]
    records[2]["status"] = "same-file-seen"
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in records) + '{"interrupted":')
    paths[1].write_bytes(b"changed")
    assert list(hp.completed_files(str(ledger))) == [str(paths[0])]


def test_harvest_report_still_updates_the_resume_ledger(harvest, monkeypatch, tmp_path):
    path, r2, *_ = harvest
    ledger, report = tmp_path / "new" / "ledger.jsonl", tmp_path / "report.jsonl"
    monkeypatch.setattr(hp, "LEDGER", str(ledger))
    monkeypatch.setattr(dp, "r2_cache", lambda: r2)
    monkeypatch.setattr(hp, "bucket_names", lambda r2: set())
    monkeypatch.setattr(sys, "argv", ["harvest_pdfs.py", str(path.parent), "--report", str(report)])
    hp.main()
    assert json.loads(ledger.read_text())["status"] == "uploaded"
    assert ledger.read_text() == report.read_text()
    assert str(path) in hp.completed_files(str(ledger))
