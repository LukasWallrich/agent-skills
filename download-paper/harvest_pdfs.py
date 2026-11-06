#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fetchpdf @ git+https://github.com/The-Metascience-Observatory/fetchpdf@82a4c474178a3e4245da82542c3a25434674e48e",
#   "requests",
#   "ddgs",
#   "boto3",
# ]
# ///
"""Collect papers scattered across a machine into the shared R2 cache.

Which tool fetched a file is not recorded anywhere, so a file qualifies by content: it
is uploaded only if a DOI from its file name or front pages passes the skill's identity
check against Crossref. Drafts, slides and receipts have no such match and stay put.
Files are read, never moved or changed. Progress goes to a ledger, so re-running resumes.

    harvest_pdfs.py ~/Documents --exclude flora-extractor --dry-run
    harvest_pdfs.py --zotero          # stored Zotero PDFs, trying their item's DOI first
"""

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import random
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_paper as dp

LEDGER = os.path.expanduser("~/.claude/cache/pdfs/harvest-ledger.jsonl")
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", ".cache", "Library", ".Trash",
             "site-packages", ".npm", ".local", ".nvm", ".rustup", ".cargo", "renv", "micromamba",
             "miniconda3", ".TinyTeX", ".t3", ".dvc", "cloud-data"}
DOI_RX = re.compile(r"10\.\d{4,9}/[^\s\"<>{}]+")
FILENAME_DOI_RX = re.compile(r"(10\.\d{4,9})(?:--|[_/])(.+)$")
MAX_CANDIDATES = 4
CROSSREF_SPACING = 0.25     # seconds between Crossref calls in this process
_crossref_lock = threading.Lock()
_crossref_last = [0.0]


def find_pdfs(roots, exclude):
    for root in roots:
        for folder, dirs, files in os.walk(os.path.expanduser(root)):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in exclude]
            if any(part in exclude for part in folder.split(os.sep)):
                dirs[:] = []
                continue
            for name in files:
                if name.lower().endswith(".pdf"):
                    yield os.path.join(folder, name)


ZOTERO_DIR = os.path.expanduser("~/Zotero")
ZOTERO_PDFS = """
    SELECT a.key, ia.path, ia.linkMode, v.value FROM itemAttachments ia
    JOIN items a ON a.itemID = ia.itemID
    LEFT JOIN itemData d ON d.itemID = ia.parentItemID
         AND d.fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'DOI')
    LEFT JOIN itemDataValues v ON v.valueID = d.valueID
    WHERE ia.contentType = 'application/pdf' AND ia.linkMode IN (0, 1, 2)
      AND a.itemID NOT IN (SELECT itemID FROM deletedItems)
      AND (ia.parentItemID IS NULL OR ia.parentItemID NOT IN (SELECT itemID FROM deletedItems))"""


def zotero_pdfs(zotero_dir=ZOTERO_DIR):
    """{path: DOI of the parent item or ""} for every Zotero PDF on this disk, all libraries.

    Uses a read-only connection, including committed changes in an open Zotero's WAL."""
    uri = Path(zotero_dir, "zotero.sqlite").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(ZOTERO_PDFS).fetchall()
    found = {}
    for key, stored, link_mode, doi in rows:
        if not stored:
            continue
        if stored.startswith("storage:"):
            path = os.path.join(zotero_dir, "storage", key, stored[len("storage:"):])
        elif link_mode == 2:
            path = stored
        else:
            continue
        if os.path.isfile(path):
            found[path] = (doi or "").strip()
    return found


def candidates(path, hint=""):
    """DOIs worth checking: a known DOI, the file name's, then those printed earliest."""
    found = DOI_RX.findall(hint)[:1]
    stem = os.path.basename(path)[:-4]
    match = FILENAME_DOI_RX.search(stem)
    if match:
        found.append(f"{match.group(1)}/{match.group(2)}")
    try:
        text = subprocess.run(["pdftotext", "-l", "2", path, "-"], capture_output=True, text=True,
                              errors="ignore", timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    found += DOI_RX.findall(text)
    clean = []
    for doi in found:
        doi = doi.rstrip(".,;:)]}'").lower()
        if doi not in clean:
            clean.append(doi)
    return clean[:MAX_CANDIDATES]


def title_record(doi):
    with _crossref_lock:
        wait = _crossref_last[0] + CROSSREF_SPACING - time.time()
        if wait > 0:
            time.sleep(wait)
        _crossref_last[0] = time.time()
    return dp.crossref_metadata(doi)


def harvest_one(path, r2, present, seen, seen_lock, dry_run, device, hint=""):
    with open(path, "rb") as f:
        head = f.read(5)
        if head != b"%PDF-":
            return {"status": "not-a-pdf"}
        digest = hashlib.md5(head + f.read()).hexdigest()
    with seen_lock:
        if digest in seen:
            return {"status": "same-file-seen", "md5": digest}
    for doi in candidates(path, hint):
        name = os.path.basename(dp.doi_to_cache_path(doi, ""))
        meta = title_record(doi)
        if not meta.get("title"):
            continue
        if not dp.verify_identity(path, doi, meta["title"], meta.get("pages")).ok:
            continue
        with seen_lock:
            if name in present:
                seen.add(digest)
                return {"status": "doi-already-held", "doi": doi, "md5": digest}
            # Only announce a DOI as held after its upload succeeds. Keeping the
            # upload under this lock avoids another worker recording a failed upload
            # as a completed duplicate.
            if not dry_run and not r2.store(path, name, doi=doi, route=f"harvest-{device}",
                                            fetched=time.strftime("%Y-%m-%d")):
                return {"status": "upload-failed", "doi": doi, "md5": digest}
            present.add(name)
            seen.add(digest)
        return {"status": "would-upload" if dry_run else "uploaded", "doi": doi, "md5": digest,
                "title": meta["title"][:120]}
    return {"status": "no-verified-doi", "md5": digest}


def bucket_names(r2):
    names = set()
    pages = r2.client.get_paginator("list_objects_v2").paginate(Bucket=r2.bucket, Prefix=r2.prefix + "/")
    for page in pages:
        names |= {os.path.basename(o["Key"]) for o in page.get("Contents", [])}
    return names


def file_stamp(path):
    stat = os.stat(path)
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def completed_files(ledger):
    """Only successful, unchanged files can be skipped on the next run."""
    latest = {}
    if not os.path.exists(ledger):
        return latest
    with open(ledger) as f:
        for line in f:
            try:
                record = json.loads(line)
                latest[record["path"]] = record
            except (ValueError, KeyError, TypeError):
                continue  # A stopped run may leave a partial final line.
    done = {}
    for path, record in latest.items():
        if record.get("status") not in {"uploaded", "doi-already-held"}:
            continue
        try:
            if record.get("stamp") == file_stamp(path):
                done[path] = record
        except OSError:
            continue
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--zotero", action="store_true",
                    help="Also check the PDFs stored in Zotero (~/Zotero), trying their item's DOI first")
    ap.add_argument("--exclude", action="append", default=[], help="Folder name to skip; repeatable")
    ap.add_argument("--dry-run", action="store_true", help="Check files without uploading or using the ledger")
    ap.add_argument("--sample", type=int, help="Check this many randomly chosen files")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", help="Write each file's result here as JSON lines")
    args = ap.parse_args()
    if args.workers < 1 or (args.sample is not None and args.sample < 1):
        ap.error("--workers and --sample must be positive")

    r2 = dp.r2_cache()
    if not r2:
        raise SystemExit("Set PAPER_CACHE_R2_BUCKET and the R2_* credentials first.")
    exclude = set(args.exclude)
    cache_dir = os.path.realpath(dp.DEFAULT_CACHE_DIR)
    hints = zotero_pdfs() if args.zotero else {}
    if not args.roots and not args.zotero:
        ap.error("give folders to search, --zotero, or both")
    paths = list(dict.fromkeys(os.path.abspath(p) for p in find_pdfs(args.roots, exclude)
                               if os.path.commonpath([cache_dir, os.path.realpath(p)]) != cache_dir))
    listed = set(paths)
    paths += [p for p in hints if p not in listed]
    done = {} if args.dry_run else completed_files(LEDGER)
    paths = [p for p in paths if p not in done]
    if args.sample is not None:
        paths = random.sample(paths, min(args.sample, len(paths)))
    present = bucket_names(r2)
    seen = {r["md5"] for r in done.values() if r.get("md5")}
    lock = threading.Lock()
    device = socket.gethostname().split(".")[0]
    counts = {}
    log_files = []

    def work(path):
        try:
            stamp = file_stamp(path)
            result = harvest_one(path, r2, present, seen, lock, args.dry_run, device, hints.get(path, ""))
            result["stamp"] = stamp
        except Exception as error:
            result = {"status": "error", "error": f"{type(error).__name__}: {error}"[:200]}
        result["path"] = path
        with lock:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            for log_file in log_files:
                log_file.write(json.dumps(result) + "\n")
                log_file.flush()
            total = sum(counts.values())
            if total % 500 == 0:
                print(f"{total}/{len(paths)} {counts}", file=sys.stderr, flush=True)

    log_paths = ([LEDGER] if not args.dry_run else []) + ([args.report] if args.report else [])
    with contextlib.ExitStack() as stack:
        for path in dict.fromkeys(os.path.abspath(p) for p in log_paths):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            incomplete = False
            if os.path.exists(path) and os.path.getsize(path):
                with open(path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    incomplete = f.read(1) != b"\n"
            log_file = stack.enter_context(open(path, "a"))
            if incomplete:
                log_file.write("\n")
            log_files.append(log_file)
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(work, paths))
    print(json.dumps({"files": len(paths), **counts}))
    if r2.off:
        raise SystemExit("R2 errors stopped uploads; re-run to continue.")


if __name__ == "__main__":
    main()
