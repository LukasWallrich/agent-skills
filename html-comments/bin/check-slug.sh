#!/usr/bin/env bash
# Decide whether a project slug is safe to use, BEFORE it is embedded in a page,
# and optionally reserve it.
#
#   bin/check-slug.sh my-report-v2
#   bin/check-slug.sh my-report --claim https://my-report.surge.sh/
#   bin/check-slug.sh my-report --allow-existing-at https://my-report.surge.sh/
#   bin/check-slug.sh my-report --force
#   bin/check-slug.sh my-report --release      # drop a reservation (admin token)
#
# A slug names the sheet tab that holds a document's comments. Two documents on
# one tab corrupts both sets: each page tries to anchor the other's comments into
# text it does not contain. Nothing downstream detects this, and it cannot be
# repaired by editing — so the check belongs here, before the slug is used.
#
# Two sources say a slug is taken, and both are needed:
#   * the _slugs registry — reserved at deploy/embed time, so it covers a page
#     that is live but has no comments yet;
#   * the tab list — covers everything claimed before the registry existed.
#
# Exit 0 = safe to use. Exit 3 = already taken. Exit 1 = could not tell (missing
# config, endpoint unreachable) — also not safe.
set -euo pipefail

CFG="$HOME/.claude/html-comments.config.json"

SLUG=""; ALLOW_AT=""; CLAIM_URL=""; FORCE=0; RELEASE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-existing-at) ALLOW_AT="${2:-}"; shift 2 ;;
    --claim)             CLAIM_URL="${2:-}"; shift 2 ;;
    --force)             FORCE=1; shift ;;
    --release)           RELEASE=1; shift ;;
    -h|--help)
      echo "Usage: check-slug.sh <slug> [--claim <url>] [--allow-existing-at <url>] [--release] [--force]" >&2; exit 1 ;;
    *) [ -z "$SLUG" ] || { echo "Only one slug may be given." >&2; exit 1; }
       SLUG="$1"; shift ;;
  esac
done
[ -n "$SLUG" ] || { echo "Usage: check-slug.sh <slug> [--claim <url>] [--allow-existing-at <url>] [--release] [--force]" >&2; exit 1; }

printf '%s' "$SLUG" | grep -Eq '^[a-z0-9][a-z0-9-]{2,89}$' || {
  echo "Slug '$SLUG' is not usable: 3-90 chars of [a-z0-9-], starting alphanumeric." >&2
  exit 1
}

# Compared against registry entries, where a stored URL may or may not have kept
# its trailing slash.
norm_url() { printf '%s' "${1%/}"; }
ALLOW_AT_N=$(norm_url "$ALLOW_AT")
CLAIM_URL_N=$(norm_url "$CLAIM_URL")

if [ "$FORCE" = 1 ]; then
  echo "--force: using slug '$SLUG' without checking."
  exit 0
fi

[ -f "$CFG" ] || { echo "Missing $CFG — cannot check slug reuse." >&2; exit 1; }
ENDPOINT=$(python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
v = cfg.get("endpoint")
if not v: sys.exit("no \"endpoint\" key in " + sys.argv[1])
print(v)
' "$CFG") || exit 1

# After a burst of requests the endpoint 404s reads for a while, so a single
# failed fetch says nothing about the slug. Retry before concluding anything.
fetch_retry() {
  local url="$1" out delay=2 i
  for i in 1 2 3 4; do
    if out=$(curl -fsSL --max-time 25 "$url" 2>/dev/null); then
      printf '%s' "$out"
      return 0
    fi
    [ "$i" = 4 ] && break
    sleep "$delay"
    delay=$((delay * 2))
  done
  return 1
}

unreachable() {
  echo "Could not reach the comments endpoint to check whether '$SLUG' is in use." >&2
  echo "Do not embed the slug until this can be checked, or pass --force." >&2
  exit 1
}

# Who holds this slug in the registry? Empty means nobody.
registry_holder() {
  local body
  body=$(fetch_retry "$ENDPOINT?action=slugs") || return 1
  printf '%s' "$body" | python3 -c '
import json, sys
try:
    slugs = json.load(sys.stdin).get("slugs") or []
except Exception:
    sys.exit(2)
for s in slugs:
    if s.get("slug") == sys.argv[1]:
        print((s.get("url") or "").rstrip("/"))
        break
' "$SLUG"
}

if [ "$RELEASE" = 1 ]; then
  TOKEN=$(python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
v = cfg.get("adminToken")
if not v: sys.exit("no \"adminToken\" in " + sys.argv[1] + " — releasing needs it")
print(v)
' "$CFG") || exit 1

  # Comments are the thing a release must never strand, so say so before asking.
  TABS=$(fetch_retry "$ENDPOINT?action=projects") || unreachable
  HAS_TAB=$(printf '%s' "$TABS" | python3 -c '
import json, sys
print("yes" if sys.argv[1] in (json.load(sys.stdin).get("projects") or []) else "no")
' "$SLUG")
  if [ "$HAS_TAB" = "yes" ]; then
    echo "Slug '$SLUG' has comments, so it is not released — that guard is deliberate." >&2
    echo "Its reservation is what stops a second document being pointed at that feedback." >&2
    exit 3
  fi

  curl -sL --max-time 30 --data-binary "$(python3 -c '
import json, sys
print(json.dumps({"action": "release", "project": sys.argv[1], "token": sys.argv[2]}))
' "$SLUG" "$TOKEN")" -H 'Content-Type: text/plain;charset=utf-8' "$ENDPOINT" >/dev/null 2>&1 || true

  # The reply is often lost to the 302 quirk, so the registry decides.
  sleep 2
  HOLDER=$(registry_holder) || { echo "Could not re-read the registry to confirm." >&2; exit 1; }
  if [ -z "$HOLDER" ]; then
    echo "Slug '$SLUG' is not reserved — the name is free."
    exit 0
  fi
  echo "Slug '$SLUG' is still registered to $HOLDER." >&2
  echo "The release was refused; check the admin token." >&2
  exit 3
fi

HOLDER=$(registry_holder) || unreachable

if [ -n "$HOLDER" ]; then
  if [ -n "$ALLOW_AT_N" ] && [ "$HOLDER" = "$ALLOW_AT_N" ]; then
    echo "Slug '$SLUG' is registered to this same document ($HOLDER) — fine to reuse."
    exit 0
  fi
  if [ -n "$CLAIM_URL_N" ] && [ "$HOLDER" = "$CLAIM_URL_N" ]; then
    echo "Slug '$SLUG' is already registered to $HOLDER — that is this document."
    exit 0
  fi
  echo "Slug '$SLUG' is already claimed by $HOLDER." >&2
  echo "Using it would mix the comments of two documents, which cannot be repaired." >&2
  echo "Pick a fresh slug, such as $SLUG-v2. Use --force only if this really is that document." >&2
  exit 3
fi

# Not in the registry — fall back to the tab list, which covers slugs in use
# since before the registry existed.
TABS=$(fetch_retry "$ENDPOINT?action=projects") || unreachable
STATE=$(printf '%s' "$TABS" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit("unparseable response from the endpoint")
tabs = data.get("projects") or []
print("yes" if sys.argv[1] in tabs else "no", len(tabs))
' "$SLUG") || exit 1
IN_USE=${STATE% *}
TABS_USED=${STATE#* }

# One spreadsheet holds at most 200 sheets, and each tab is created at the
# default 1000x26 = 26,000 cells against a 10,000,000-cell budget — so the sheet
# count runs out (~200) before the cells do (~384). Only documents that actually
# receive a comment consume one.
if [ "$TABS_USED" -ge 150 ]; then
  echo "Warning: $TABS_USED of ~200 tabs used in the comments spreadsheet." >&2
  echo "Approaching the per-spreadsheet sheet limit — retire old tabs or start a second sheet." >&2
fi

if [ "$IN_USE" = "yes" ]; then
  # A tab with comments, and no registry entry saying it is this document.
  if [ -n "$ALLOW_AT_N" ]; then
    BODY=$(curl -sS --max-time 30 "$ALLOW_AT" 2>/dev/null || true)
    if printf '%s' "$BODY" | grep -q "hc-doc: *$SLUG" \
       || printf '%s' "$BODY" | grep -q "project: *'$SLUG'"; then
      echo "Slug '$SLUG' is in use by this same document ($ALLOW_AT) — fine to reuse."
      exit 0
    fi
  fi
  echo "Slug '$SLUG' already has comments from another document." >&2
  echo "Using it would mix the comments of two documents, which cannot be repaired." >&2
  echo "Pick a fresh slug, such as $SLUG-v2. Use --force only if this really is that document." >&2
  exit 3
fi

# Free. Reserve it if asked, then confirm we are the holder — a claim whose
# response was lost still wrote the row, so the registry, not the reply, decides.
if [ -n "$CLAIM_URL" ]; then
  curl -sL --max-time 30 --data-binary "$(python3 -c '
import json, sys
print(json.dumps({"action": "claim", "project": sys.argv[1], "url": sys.argv[2]}))
' "$SLUG" "$CLAIM_URL")" -H 'Content-Type: text/plain;charset=utf-8' "$ENDPOINT" >/dev/null 2>&1 || true
  sleep 2
  HOLDER=$(registry_holder) || unreachable
  if [ "$HOLDER" != "$CLAIM_URL_N" ]; then
    echo "Claimed '$SLUG' for $CLAIM_URL, but the registry now shows '${HOLDER:-nothing}'." >&2
    echo "Someone else may have taken it — pick another slug." >&2
    exit 3
  fi
  echo "Slug '$SLUG' claimed for $CLAIM_URL ($TABS_USED of ~200 tabs used)."
  exit 0
fi

echo "Slug '$SLUG' is free ($TABS_USED of ~200 tabs used)."
exit 0
