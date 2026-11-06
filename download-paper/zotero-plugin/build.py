#!/usr/bin/env python3
"""Build dist/find-pdf-api-<version>.xpi and update.json from src/.

The same source gives the same bytes on any machine, as nothing is compressed. A released version never changes: if src/ differs
from the .xpi already built for this version, the build stops and asks for a new version
in src/manifest.json, because Zotero only offers an update when the version goes up.

    python3 build.py           build, and refresh update.json
    python3 build.py --check   fail unless dist/ and update.json match src/
"""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
DIST = HERE / "dist"
UPDATE_JSON = HERE / "update.json"
RAW_BASE = "https://raw.githubusercontent.com/LukasWallrich/agent-skills/main/download-paper/zotero-plugin"
ZIP_EPOCH = (2020, 1, 1, 0, 0, 0)


def manifest():
    return json.loads((SRC / "manifest.json").read_text())


def xpi_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(p for p in SRC.rglob("*") if p.is_file() and p.name != ".DS_Store"):
            info = zipfile.ZipInfo(path.relative_to(SRC).as_posix(), ZIP_EPOCH)
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def update_manifest(data, info):
    settings = info["applications"]["zotero"]
    name = f"find-pdf-api-{info['version']}.xpi"
    return {"addons": {settings["id"]: {"updates": [{
        "version": info["version"],
        "update_link": f"{RAW_BASE}/dist/{name}",
        "update_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        "applications": {"zotero": {
            "strict_min_version": settings["strict_min_version"],
            "strict_max_version": settings["strict_max_version"],
        }},
    }]}}}


def main():
    info = manifest()
    target = DIST / f"find-pdf-api-{info['version']}.xpi"
    data = xpi_bytes()
    update_text = json.dumps(update_manifest(data, info), indent=2) + "\n"
    same_xpi = target.exists() and target.read_bytes() == data
    if "--check" in sys.argv:
        if not same_xpi or not UPDATE_JSON.exists() or UPDATE_JSON.read_text() != update_text:
            sys.exit("dist/ or update.json is out of date: run build.py")
        print(f"{target.name} and update.json match src/")
        return
    if target.exists() and not same_xpi:
        sys.exit(f"{target.name} already exists with other contents: "
                 "raise the version in src/manifest.json")
    DIST.mkdir(exist_ok=True)
    target.write_bytes(data)
    UPDATE_JSON.write_text(update_text)
    print(target)


if __name__ == "__main__":
    main()
