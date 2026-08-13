#!/usr/bin/env python3
"""Resolve (or reopen) html-comments threads — optionally replying first — and
verify it actually took.

Posting records by hand gets silently swallowed in three different ways (see
"Resolving threads programmatically" in SKILL.md). This script gets all three
right, survives the endpoint's two failure modes (below), and then re-reads the
log using the *overlay's* reduction rules, so "resolved" here means the
reviewer's sidebar will agree.

Usage:
    python3 bin/resolve.py <project> --open               # list open thread ids
    python3 bin/resolve.py <project> --all                # resolve every open thread
    python3 bin/resolve.py <project> id1 id2 ...          # resolve specific threads
    python3 bin/resolve.py <project> --all --reopen       # reopen every RESOLVED thread
    python3 bin/resolve.py <project> --all --voter "Lukas (applied)"
    python3 bin/resolve.py <project> --all --reply-file answers.json
        # answers.json: {"<thread itemId>": "reply text", ...}
        # posts each reply, then the resolve, then verifies — one call.
        # With --reply-file, ONLY threads present in the file are targeted
        # (--all additionally downgrades unknown keys from an error to a
        # warning, so a stale answers.json still applies the rest).

Direction of travel:
  * default    — targets OPEN threads and posts `resolve`
  * --reopen   — targets RESOLVED threads and posts `reopen`
Both `--all` and `--reply-file` keys are matched against the set appropriate to
the direction.

Target handling:
  * An explicit id (or reply-file key) naming a thread that is already in the
    desired state is noted on stderr and dropped from the run — never an error,
    with or without --all, so reruns are safe.
  * An explicit id that is not a thread root at all (unknown, deleted, or a typo)
    is a hard error: nothing is posted and the exit status is non-zero. The one
    exception is `--all --reply-file`, where unknown keys are warned about and
    skipped and the remaining threads are still processed.
  * If every target was already in the desired state, the script prints
    "nothing to do (all targeted threads already in the desired state)" and exits
    0 — reruns are safe.

Verification (the only source of truth) re-reads the log once at the end and
requires:
  * every targeted thread to be RESOLVED (default) or OPEN (--reopen);
  * every reply record posted in this run to appear in the returned rows.
Any shortfall is reported and exits non-zero.

The endpoint is read from ~/.claude/html-comments.config.json.

Endpoint quirks this script absorbs (do not "fix" them away):
  * A successful POST is answered with a 302 to a one-time Google URL that
    frequently 404s. The row is already in the sheet at that point, so an
    HTTPError from a POST means "unknown", never "failed" — the re-read at the
    end is the only truth. That is why post() swallows HTTP errors.
  * Bursts get rate-limited (reads AND writes start 404ing), so every request
    retries with backoff and posts are spaced out.
"""

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG = pathlib.Path.home() / ".claude" / "html-comments.config.json"

POST_SPACING_S = 1.0          # bursts trip the endpoint's rate limiting
RETRIES = 4                   # per request, with 5s/10s/20s backoff (none after the last try)
BACKOFF_BASE_S = 5


def endpoint() -> str:
    if not CONFIG.exists():
        sys.exit(
            f"missing {CONFIG}\n"
            "Create it with your own values:\n"
            '  {"endpoint": "https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec",\n'
            '   "assetBase": "https://your-asset-host.example/",\n'
            '   "publishTarget": "your-asset-host.example"}'
        )
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"could not parse {CONFIG}: {e}")
    ep = cfg.get("endpoint")
    if not ep:
        sys.exit(f'{CONFIG} has no "endpoint" key — it needs '
                 '{"endpoint": …, "assetBase": …, "publishTarget": …}')
    return ep


def rows_url(ep: str, project: str) -> str:
    # An endpoint URL may already carry a query string (?foo=bar); appending
    # another '?' would break it.
    sep = "&" if "?" in ep else "?"
    return f"{ep}{sep}action=rows&project={urllib.parse.quote(project)}"


def fetch_rows(ep: str, project: str) -> list:
    url = rows_url(ep, project)
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:  # follows the 302 itself
                payload = json.load(r)
            if not payload.get("ok"):
                sys.exit(f"read failed: {payload}")
            return payload["rows"]
        except (urllib.error.URLError, json.JSONDecodeError,
                UnicodeDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_BASE_S * 2 ** attempt)
    sys.exit(f"read failed after {RETRIES} attempts: {last_err}")


def parse_note(row) -> dict:
    try:
        return json.loads(row.get("note") or "{}")
    except Exception:
        return {}


def reduce_threads(rows: list) -> tuple:
    """Reduce exactly as assets/html-comments.js does, so this agrees with the UI.

    Returns (open_ids, resolved_ids) — both lists, in root order, with deleted
    threads excluded from each.

    Two rules matter and both differ from a naive reduction:
      * composeRows() dedupes on itemId keeping the FIRST occurrence, so a record
        reusing an existing itemId is dropped before it is ever interpreted;
      * buildThreads() reads resolve/reopen targets from note.parentId ONLY, with
        no fallback to the record's own itemId.
    """
    seen, kept = set(), []
    for row in rows:
        item_id = row.get("itemId")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        kept.append(row)

    roots, status, deleted = [], {}, set()
    for row in kept:
        note = parse_note(row)
        vote = row.get("vote")
        if vote in ("comment", "suggestion"):
            roots.append(row["itemId"])
        elif vote in ("resolve", "reopen"):
            target = note.get("parentId")           # no `or row["itemId"]` fallback
            if target is not None:
                status[target] = vote
        elif vote == "delete":
            if note.get("parentId"):
                deleted.add(note["parentId"])

    live = [t for t in roots if t not in deleted]
    return ([t for t in live if status.get(t) != "resolve"],
            [t for t in live if status.get(t) == "resolve"])


def open_threads(rows: list) -> list:
    """Thread roots the overlay would show as open."""
    return reduce_threads(rows)[0]


def resolved_threads(rows: list) -> list:
    """Thread roots the overlay would show as resolved."""
    return reduce_threads(rows)[1]


def select_targets(ids: list, want_open: list, want_resolved: list) -> tuple:
    """Classify requested thread ids against the two reduced sets.

    `want_open` is the set an id must be in to be actionable; `want_resolved` is
    the set of ids already in the desired state. Anything in neither is unknown.

    Returns (targets, already_done, unknown), preserving the requested order.
    """
    actionable, done = set(want_open), set(want_resolved)
    targets, already, unknown = [], [], []
    for t in ids:
        if t in actionable:
            targets.append(t)
        elif t in done:
            already.append(t)
        else:
            unknown.append(t)
    return targets, already, unknown


def post(ep: str, payload: dict) -> None:
    """Post one record. Absorbs the 302→404 (the row is already written by then).

    Returns nothing on purpose: neither {"ok":true} nor an HTTP error is evidence
    of what landed. main() verifies by re-reading the log afterwards.
    """
    req = urllib.request.Request(
        ep,
        data=json.dumps(payload).encode("utf-8"),
        # MUST be a JSON body with this content type. A form-encoded POST is
        # answered {"ok":true} and appends nothing at all.
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            return
        except urllib.error.HTTPError:
            # The write path answers 302 → one-time URL → often 404. By the time
            # any HTTP status comes back, the append has happened; retrying the
            # POST would only risk a duplicate row (harmless — itemId dedup —
            # but pointless). Treat as sent.
            return
        except (urllib.error.URLError, TimeoutError):
            # Never reached the endpoint — this one IS worth retrying.
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_BASE_S * 2 ** attempt)
    # Fall through silently: the final re-read decides whether it took.


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("threads", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="target every thread in the actionable state "
                         "(open for resolve, resolved for --reopen)")
    ap.add_argument("--open", action="store_true", help="just list open threads")
    ap.add_argument("--reopen", action="store_true", help="post reopen instead of resolve")
    ap.add_argument("--reply-file", metavar="JSON",
                    help='{"<thread id>": "reply text"} — post each reply before its resolve')
    ap.add_argument("--voter", default="applied")
    ap.add_argument("--session", default="apply-script")
    args = ap.parse_args()

    ep = endpoint()
    rows = fetch_rows(ep, args.project)
    still_open, already_resolved = reduce_threads(rows)

    if args.open:
        print("\n".join(still_open) or "(none open)")
        print(f"\n{len(still_open)} open of {len(rows)} rows", file=sys.stderr)
        if not rows:
            print(f"warning: project '{args.project}' has no rows at all — "
                  "check the slug", file=sys.stderr)
        return

    # --reopen travels the other way: it acts on resolved threads and leaves them
    # open, so "actionable" and "already in the desired state" swap over.
    actionable = already_resolved if args.reopen else still_open
    settled = still_open if args.reopen else already_resolved

    replies: dict = {}
    if args.reply_file:
        replies = json.loads(pathlib.Path(args.reply_file).read_text(encoding="utf-8"))
        requested = list(replies)
    elif args.all:
        requested = list(actionable)
    else:
        requested = list(args.threads)

    if not requested:
        sys.exit("nothing to do: pass thread ids, --all, --reply-file, or --open")

    targets, already, unknown = select_targets(requested, actionable, settled)

    if unknown and not (args.all and args.reply_file):
        # An id that is not a thread root at all is a typo or a deleted thread —
        # posting against it would write rows the overlay silently ignores.
        print(f"unknown thread id(s) for project '{args.project}' "
              f"(not a live thread root): " + ", ".join(unknown), file=sys.stderr)
        sys.exit("nothing was posted")
    if unknown:
        # `--all --reply-file` is documented as "don't error on ids already closed".
        print(f"note: {len(unknown)} reply target(s) are not live thread roots and "
              "were skipped: " + ", ".join(unknown), file=sys.stderr)
    if already:
        print(f"note: {len(already)} target(s) already "
              f"{'open' if args.reopen else 'resolved'} — skipped: "
              + ", ".join(already), file=sys.stderr)

    if not targets:
        print("nothing to do (all targeted threads already in the desired state)")
        return

    vote = "reopen" if args.reopen else "resolve"
    # ms timestamp in every id: a rerun (or a second machine) must never mint the
    # ids an earlier run already wrote — the overlay's keep-first dedup would drop
    # the new records and the threads would silently stay open (trap #2).
    stamp = int(time.time() * 1000)
    posted_replies: dict = {}          # reply itemId -> thread id
    for n, thread_id in enumerate(targets, 1):
        if thread_id in replies:
            reply_id = f"hc-reply-{stamp}-{n}"
            post(ep, {
                "project": args.project,
                "itemId": reply_id,
                "vote": "reply",
                "note": json.dumps({"v": 1, "kind": "reply",
                                    "text": replies[thread_id],
                                    "parentId": thread_id}),
                "voter": args.voter,
                "session": args.session,
            })
            posted_replies[reply_id] = thread_id
            time.sleep(POST_SPACING_S)
        payload = {
            "project": args.project,
            # A FRESH, globally unique id. Reusing thread_id here makes the
            # overlay dedupe the record away, so the thread silently stays open.
            "itemId": f"hc-{vote}-{stamp}-{n}",
            "vote": vote,
            # parentId is what buildThreads() reads. Without it the record is
            # written to the sheet and then ignored by the overlay.
            "note": json.dumps({"v": 1, "parentId": thread_id}),
            "voter": args.voter,
            "session": args.session,
        }
        post(ep, payload)
        time.sleep(POST_SPACING_S)

    # Re-read and re-reduce: an ok:true response is not evidence that a row landed,
    # and neither is an HTTP error evidence that it did not. This is the truth.
    final_rows = fetch_rows(ep, args.project)
    final_open, final_resolved = reduce_threads(final_rows)
    target_set = set(targets)

    # Only the threads we aimed at count as failures. Reviewers comment while this
    # runs, so a thread that appeared mid-run is new work, not a botched resolve.
    if args.reopen:
        failed = [t for t in targets if t not in set(final_open)]
    else:
        failed = [t for t in final_open if t in target_set]
    arrived = [t for t in final_open if t not in target_set and t not in still_open]

    landed_ids = {r.get("itemId") for r in final_rows}
    missing_replies = [rid for rid in posted_replies if rid not in landed_ids]

    print(f"posted {len(targets)} {vote} record(s)"
          + (f" with {len(posted_replies)} replies" if posted_replies else "")
          + f"; {len(failed)} of them still "
          + ("resolved" if args.reopen else "open"))
    if arrived:
        print(f"{len(arrived)} thread(s) appeared during the run and were not targeted: "
              + ", ".join(arrived))

    problems = []
    if failed:
        problems.append(
            ("targeted threads are still resolved — the reopen records did not take: "
             if args.reopen else
             "targeted threads are still open — the resolve records did not take: ")
            + ", ".join(failed))
    if missing_replies:
        problems.append(
            f"{len(missing_replies)} reply record(s) did not land (the {vote} may still "
            "have gone through) for thread(s): "
            + ", ".join(posted_replies[rid] for rid in missing_replies))
    if problems:
        sys.exit("\n".join(problems))


if __name__ == "__main__":
    main()
