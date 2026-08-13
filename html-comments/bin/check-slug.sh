#!/usr/bin/env bash
# Decide whether a project slug is safe to use, BEFORE it is embedded in a page.
#
#   bin/check-slug.sh my-report-v2
#   bin/check-slug.sh my-report --allow-existing-at https://my-report.surge.sh/
#   bin/check-slug.sh my-report --force
#
# A slug names the sheet tab that holds a document's comments. Two documents on
# one tab corrupts both sets: each page tries to anchor the other's comments into
# text it does not contain. Nothing downstream detects this, and it cannot be
# repaired by editing — so the check belongs here, before the slug is used.
#
# Exit 0 = safe to use. Exit 3 = already in use by another document. Exit 1 =
# could not tell (missing config, endpoint unreachable) — also not safe.
set -euo pipefail

CFG="$HOME/.claude/html-comments.config.json"

SLUG=""; ALLOW_AT=""; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-existing-at) ALLOW_AT="${2:-}"; shift 2 ;;
    --force)             FORCE=1; shift ;;
    -h|--help)
      echo "Usage: check-slug.sh <slug> [--allow-existing-at <url>] [--force]" >&2; exit 1 ;;
    *) [ -z "$SLUG" ] || { echo "Only one slug may be given." >&2; exit 1; }
       SLUG="$1"; shift ;;
  esac
done
[ -n "$SLUG" ] || { echo "Usage: check-slug.sh <slug> [--allow-existing-at <url>] [--force]" >&2; exit 1; }

printf '%s' "$SLUG" | grep -Eq '^[a-z0-9][a-z0-9-]{2,89}$' || {
  echo "Slug '$SLUG' is not usable: 3-90 chars of [a-z0-9-], starting alphanumeric." >&2
  exit 1
}

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

TABS=$(fetch_retry "$ENDPOINT?action=projects") || {
  echo "Could not reach the comments endpoint to check whether '$SLUG' is in use." >&2
  echo "Do not embed the slug until this can be checked, or pass --force." >&2
  exit 1
}

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

# One spreadsheet holds at most 200 sheets, and each new tab is created at the
# default 1000x26 = 26,000 cells against a 10,000,000-cell budget — so the sheet
# count runs out (~200) well before the cells do (~384). Trimming new sheets in
# Code.gs is the fix if that day comes; until then, just report the count.
if [ "$TABS_USED" -ge 150 ]; then
  echo "Warning: $TABS_USED of ~200 tabs used in the comments spreadsheet." >&2
  echo "Approaching the per-spreadsheet sheet limit — retire old tabs or start a second sheet." >&2
fi

if [ "$IN_USE" = "no" ]; then
  echo "Slug '$SLUG' is free ($TABS_USED of ~200 tabs used)."
  exit 0
fi

# In use — but the same document redeploying is the normal case, not a clash.
if [ -n "$ALLOW_AT" ]; then
  BODY=$(curl -sS --max-time 30 "$ALLOW_AT" 2>/dev/null || true)
  if printf '%s' "$BODY" | grep -q "hc-doc: *$SLUG" \
     || printf '%s' "$BODY" | grep -q "project: *'$SLUG'"; then
    echo "Slug '$SLUG' is in use by this same document ($ALLOW_AT) — fine to reuse."
    exit 0
  fi
fi

ROWS_BODY=$(fetch_retry "$ENDPOINT?action=rows&project=$SLUG" || true)
ROWS=$(printf '%s' "$ROWS_BODY" | python3 -c '
import json, sys
try:
    print(len(json.load(sys.stdin).get("rows") or []))
except Exception:
    print("an unknown number of")
' 2>/dev/null || echo "an unknown number of")

echo "Slug '$SLUG' is already in use — its tab holds $ROWS records from another document." >&2
echo "Using it would mix the comments of two documents, which cannot be repaired afterwards." >&2
echo "Pick a fresh slug, such as $SLUG-v2. Use --force only if this really is that document." >&2
exit 3
