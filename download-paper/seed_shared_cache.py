#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3", "requests"]
# ///
"""Upload this machine's PDF cache to the shared R2 cache. Safe to re-run.

Older entries are named by the MD5 of the DOI as typed, so the same paper could sit
under two names. Where a DOI printed in the PDF hashes to the file's name, the file is
uploaded under the lowercase key. Any other file is uploaded under its existing name.
Local files are never renamed or deleted. This trusts the skill's existing verified
cache; use harvest_pdfs.py for files whose identity has not already been checked.
"""

import argparse
import concurrent.futures
import hashlib
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r2_cache import R2Cache

DEFAULT_CACHE_DIR = os.path.expanduser("~/.claude/cache/pdfs")
KEY_FILE = os.path.expanduser("~/.claude/api_keys.env")
DOI_RX = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def recover_doi(path, stem):
    """The DOI whose as-typed or lowercase form hashes to `stem`, from the PDF's front pages."""
    try:
        text = subprocess.run(["pdftotext", "-l", "3", path, "-"], capture_output=True,
                              text=True, errors="ignore", timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for found in DOI_RX.findall(text):
        found = found.rstrip(".,;)]}")
        for form in (found, found.lower(), found.upper()):
            if md5(form) == stem:
                return form
    return None


def seed_one(name, cache_dir, r2):
    """Returns (status, doi known)."""
    path = os.path.join(cache_dir, name)
    stem = name[:-len(".pdf")] if name.endswith(".pdf") else None
    doi = recover_doi(path, stem) if stem else None
    if doi and md5(doi.lower()) != stem:
        name = md5(doi.lower()) + ".pdf"
    stored = r2.store(path, name, doi=doi.lower() if doi else "", route="legacy-cache")
    return stored, bool(doi)


def load_keys():
    keys = {}
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            for line in f:
                key, sep, value = line.strip().removeprefix("export ").partition("=")
                if sep and not key.startswith("#"):
                    keys[key] = value.strip("'\"")
    keys.update(os.environ)
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    args = ap.parse_args()
    r2 = R2Cache.from_keys(load_keys())
    if not r2:
        raise SystemExit("Set PAPER_CACHE_R2_BUCKET and the R2_* credentials first.")
    names = [n for n in os.listdir(args.cache_dir)
             if re.fullmatch(r"[0-9a-f]{32}\.(pdf|jats\.xml)", n)]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda n: seed_one(n, args.cache_dir, r2), names))
    print(f"{len(names)} files, {sum(s for s, _ in results)} now in the bucket, "
          f"{sum(d for _, d in results)} with a DOI recovered from the PDF")
    if r2.off:
        raise SystemExit("Stopped early after an R2 error; re-run to continue.")


if __name__ == "__main__":
    main()
