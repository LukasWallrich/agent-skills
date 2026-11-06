"""Keep the suite off the real Zotero library and the real bucket."""

import pytest


@pytest.fixture(autouse=True)
def no_shared_stores(monkeypatch, tmp_path):
    import download_paper as dp
    monkeypatch.setattr(dp, "_shared", {"r2": None, "zotero": None})
    monkeypatch.setattr(dp, "DEFAULT_CACHE_DIR", str(tmp_path / "local-cache"))
