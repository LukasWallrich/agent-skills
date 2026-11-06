"""The shared R2 cache and the Zotero library, against stand-ins for both services."""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import download_paper as dp
import zotero_local as zl
from r2_cache import R2Cache

PDF = b"%PDF-1.7\n" + b"x" * 2000
DOI = "10.1234/Mixed.Case"


class Ok:
    ok = True
    state = "verified"
    reason = ""


class Wrong:
    ok = False
    state = "wrong"
    reason = "a different paper"


class Missing(Exception):
    response = {"Error": {"Code": "404"}}


class Body:
    def __init__(self, data):
        self.data = data

    def iter_chunks(self, size):
        yield self.data

    def close(self):
        pass


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {"Body": Body(self.objects[Key][0]), "Metadata": self.objects[Key][1]}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata, **kwargs):
        self.objects[Key] = (Body.read(), Metadata)


@pytest.fixture
def r2(monkeypatch):
    cache = R2Cache(FakeS3(), "bucket")
    monkeypatch.setitem(dp._shared, "r2", cache)
    monkeypatch.setattr(dp, "crossref_metadata", lambda doi: {"title": "T", "pages": None})
    monkeypatch.setattr(dp, "verify_identity", lambda *a: Ok)
    return cache


def stop_chain(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the download chain ran")
    monkeypatch.setattr(dp, "fetchpdf_download", refuse)


def test_cache_key_ignores_doi_case(tmp_path):
    assert dp.doi_to_cache_path("10.1/AB", tmp_path) == dp.doi_to_cache_path("10.1/ab", tmp_path)


def test_legacy_entry_is_renamed_on_first_use(tmp_path, monkeypatch):
    legacy = dp.legacy_cache_path(DOI, str(tmp_path))
    with open(legacy, "wb") as f:
        f.write(PDF)
    path, _ = dp.download_paper(DOI, cache_dir=str(tmp_path))
    assert path == dp.doi_to_cache_path(DOI, str(tmp_path))
    assert os.path.exists(path) and not os.path.exists(legacy)


def test_shared_cache_saves_the_download(r2, tmp_path, monkeypatch):
    name = os.path.basename(dp.doi_to_cache_path(DOI, ""))
    r2.client.objects[f"paper-cache/{name}"] = (PDF, {"route": "chrome"})
    stop_chain(monkeypatch)
    path, _ = dp.download_paper(DOI, cache_dir=str(tmp_path))
    with open(path, "rb") as f:
        assert f.read() == PDF


def test_a_wrong_shared_copy_is_not_used(r2, tmp_path, monkeypatch):
    name = os.path.basename(dp.doi_to_cache_path(DOI, ""))
    r2.client.objects[f"paper-cache/{name}"] = (PDF, {})
    monkeypatch.setattr(dp, "verify_identity", lambda *a: Wrong)
    assert dp.find_existing(DOI, "T", None, str(tmp_path / "out.pdf")) is None
    assert not (tmp_path / "out.pdf").exists()


def test_a_download_reaches_the_shared_cache(r2, tmp_path, monkeypatch):
    def fetched(doi, path, *a):
        with open(path, "wb") as f:
            f.write(PDF)
        return True
    monkeypatch.setattr(dp, "fetchpdf_download", fetched)
    path, _ = dp.download_paper(DOI, cache_dir=str(tmp_path))
    data, metadata = r2.client.objects[f"paper-cache/{os.path.basename(path)}"]
    assert data == PDF
    assert metadata["doi"] == DOI.lower() and metadata["route"] == "fetchpdf"


def test_r2_error_switches_it_off_without_failing(tmp_path):
    class Down(FakeS3):
        def head_object(self, Bucket, Key):
            raise ConnectionError("no route")
    cache = R2Cache(Down(), "bucket")
    target = tmp_path / "f.pdf"
    target.write_bytes(PDF)
    assert cache.store(str(target), "f.pdf") is False
    assert cache.off and cache.fetch("f.pdf", str(tmp_path / "g.pdf")) is None


def test_no_bucket_setting_means_no_cache():
    assert R2Cache.from_keys({"R2_ACCESS_KEY_ID": "x"}) is None


# ---------------- Zotero ----------------
class FakeLibrary(zl.ZoteroLocal):
    """Answers the reads from a dict and records the writes."""

    def __init__(self, items=None, pdfs=None):
        super().__init__()
        self.items = items or []
        self.pdf_paths = pdfs or {}
        self.created = []
        self.attached = []
        self.untagged = []

    def find(self, doi, library="users/0"):
        doi = zl.normal_doi(doi)
        return [i for i in self.items if zl.item_has_doi(i["data"], doi)]

    def find_by_title(self, title, year, library="users/0"):
        wanted = zl.normal_title(title)
        return next((i for i in self.items if zl.normal_title(i["data"].get("title")) == wanted), None)

    def pdfs(self, key, library="users/0"):
        return self.pdf_paths.get(key, [])

    def create_item(self, csl, doi, has_pdf):
        self.created.append((doi, has_pdf))
        return "NEW", {"title": csl["title"]}

    def attach(self, parent, path, filename):
        self.attached.append((parent, path))
        return "ATT"

    def remove_tag(self, key, tag):
        self.untagged.append((key, tag))


CSL = {"type": "journal-article", "title": "A Paper", "issued": {"date-parts": [[2024]]}}


def add(lib, pdf="/tmp/p.pdf", csl=CSL):
    return lib.add_paper(DOI, pdf, lambda doi: csl)


def test_doi_in_the_extra_field_counts():
    assert zl.item_has_doi({"DOI": "", "extra": "tex.x: y\nDOI: 10.1234/MIXED.case"}, DOI.lower())


def test_a_citing_paper_is_not_a_match():
    assert not zl.item_has_doi({"DOI": "10.9/other", "extra": ""}, DOI.lower())


def test_item_with_pdf_is_left_alone():
    lib = FakeLibrary([{"key": "A", "data": {"DOI": DOI.lower()}}], {"A": ["/x.pdf"]})
    assert add(lib) == ("already", "A")
    assert not lib.attached and not lib.created


def test_pdf_goes_on_the_existing_item_rather_than_a_duplicate():
    lib = FakeLibrary([{"key": "A", "data": {"DOI": DOI.lower(), "title": "A Paper"}}])
    assert add(lib) == ("attached", "A")
    assert lib.attached == [("A", "/tmp/p.pdf")] and not lib.created
    assert lib.untagged == [("A", "needs-pdf")]


def test_title_match_catches_a_preprint_under_another_doi():
    lib = FakeLibrary([{"key": "P", "data": {"DOI": "10.31234/x", "title": "A paper"}}])
    assert add(lib) == ("attached", "P")
    assert not lib.created


def test_new_paper_is_created_with_its_pdf():
    lib = FakeLibrary()
    assert add(lib) == ("added", "NEW")
    assert lib.created == [(DOI.lower(), True)] and lib.attached[0][0] == "NEW"


def test_failed_download_still_adds_the_record():
    lib = FakeLibrary()
    assert add(lib, pdf=None) == ("added", "NEW")
    assert lib.created == [(DOI.lower(), False)] and not lib.attached


def test_no_metadata_creates_nothing():
    lib = FakeLibrary()
    with pytest.raises(zl.ZoteroError):
        lib.add_paper(DOI, "/tmp/p.pdf", lambda doi: None)
    assert not lib.created


def test_csl_mapping_respects_the_item_type():
    csl = {"type": "posted-content", "title": ["<i>A</i> Paper"], "subtitle": ["Sub"],
           "issued": {"date-parts": [[2024, 3]]}, "publisher": "PsyArXiv", "volume": "9",
           "author": [{"given": "Ada", "family": "Byron"}, {"name": "A Team"}]}
    item = zl.csl_to_item(csl, "10.1/x", {"title", "date", "DOI", "repository", "url"}, {"author"})
    assert item["itemType"] == "preprint" and item["title"] == "A Paper: Sub"
    assert item["date"] == "2024-03" and item["repository"] == "PsyArXiv" and "volume" not in item
    assert item["creators"][0]["lastName"] == "Byron" and item["creators"][1]["name"] == "A Team"


def test_doi_goes_in_extra_when_the_type_has_no_field():
    item = zl.csl_to_item({"type": "book", "title": "B"}, "10.1/x", {"title"}, {"author"})
    assert item["extra"] == "DOI: 10.1/x" and "DOI" not in item


def test_filename_is_safe():
    name = zl.pdf_filename({"creators": [{"lastName": "Byron"}, {"lastName": "B"}], "date": "2024-03",
                            "title": "What/is: this?"})
    assert name == "Byron et al. - 2024 - What is this.pdf"


def test_add_to_zotero_failure_leaves_the_run_alone(monkeypatch, capsys):
    monkeypatch.setitem(dp._shared, "zotero", None)
    dp.add_to_zotero(DOI, "/tmp/p.pdf")
    assert "ZOTERO=not-added" in capsys.readouterr().err


# ---------------- harvest: Zotero PDFs ----------------
def test_zotero_pdfs_maps_stored_and_linked_files_to_parent_doi(tmp_path, monkeypatch):
    import sqlite3
    import harvest_pdfs as hp
    monkeypatch.setattr(hp.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": ""})())
    stored = tmp_path / "storage" / "AAAA1111" / "Smith - 2020 - Title.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"%PDF-1.4")
    linked = tmp_path / "linked.pdf"
    linked.write_bytes(b"%PDF-1.4")
    db = sqlite3.connect(tmp_path / "zotero.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(f"""
        CREATE TABLE items (itemID INTEGER, key TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER, contentType TEXT,
                                      path TEXT, linkMode INTEGER);
        CREATE TABLE fields (fieldID INTEGER, fieldName TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE itemDataValues (valueID INTEGER, value TEXT);
        CREATE TABLE deletedItems (itemID INTEGER);
        INSERT INTO fields VALUES (8, 'DOI');
        INSERT INTO itemDataValues VALUES (1, 'https://doi.org/10.1000/ABC.1');
        INSERT INTO itemData VALUES (10, 8, 1);
        INSERT INTO items VALUES (11, 'AAAA1111'), (12, 'BBBB2222'), (13, 'CCCC3333');
        INSERT INTO itemAttachments VALUES
            (11, 10, 'application/pdf', 'storage:Smith - 2020 - Title.pdf', 1),
            (12, NULL, 'application/pdf', '{linked}', 2),
            (13, 10, 'application/pdf', 'storage:missing.pdf', 1);
    """)
    db.commit()
    found = hp.zotero_pdfs(str(tmp_path))
    assert found == {str(stored): "https://doi.org/10.1000/ABC.1", str(linked): ""}
    assert hp.candidates(str(stored), found[str(stored)]) == ["10.1000/abc.1"]
    db.execute("INSERT INTO deletedItems VALUES (10)")
    db.commit()
    assert hp.zotero_pdfs(str(tmp_path)) == {str(linked): ""}
    db.close()
