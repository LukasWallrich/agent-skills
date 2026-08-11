#!/usr/bin/env bash
# Publish the canonical html-comments assets to the asset host named in
# ~/.claude/html-comments.config.json (`publishTarget`).
#
# Every report loads the overlay from that host, so this is the ONLY place a
# change to html-comments.js / .css needs to be made and shipped. A static host
# that serves `cache-control: max-age=0, must-revalidate` (surge.sh does) makes
# a publish take effect on the next page load everywhere — no report needs
# re-rendering.
#
# The target is deliberately NOT overridable from the command line: a typo would
# quietly create a fresh surge domain while the real asset host stayed stale.
set -euo pipefail

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$SKILL/assets"
CFG="$HOME/.claude/html-comments.config.json"

if [ $# -ge 1 ]; then
  echo "publish.sh takes no arguments; the target comes from $CFG (publishTarget)." >&2
  exit 1
fi

if [ ! -f "$CFG" ]; then
  echo "Missing $CFG — create it with:" >&2
  echo '  {"endpoint": "https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec",' >&2
  echo '   "assetBase": "https://your-asset-host.example/",' >&2
  echo '   "publishTarget": "your-asset-host.example"}' >&2
  exit 1
fi

DOMAIN=$(python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
t = cfg.get("publishTarget")
if not t:
    sys.exit("no \"publishTarget\" key in " + sys.argv[1])
print(t)
' "$CFG") || { echo "Could not read publishTarget from $CFG" >&2; exit 1; }

command -v node >/dev/null 2>&1 || { echo "node not found on PATH — needed to syntax-check the overlay and to run surge." >&2; exit 1; }
command -v surge >/dev/null 2>&1 || { echo "surge CLI not found on PATH — install it with 'npm install -g surge'." >&2; exit 1; }

# Extract the HC_VERSION string from a copy of html-comments.js on stdin.
# Prints nothing and returns non-zero if it is not there.
extract_version() {
  local line
  line=$(grep -m1 "var HC_VERSION" || true)
  [ -n "$line" ] || return 1
  printf '%s\n' "$line" | sed "s/.*'\(.*\)'.*/\1/"
}

live_version() {
  local body
  body=$(curl -fsS "https://$DOMAIN/html-comments.js") || return 1
  printf '%s\n' "$body" | extract_version
}

VERSION=$(extract_version < "$DIR/html-comments.js") || {
  echo "Could not find 'var HC_VERSION' in $DIR/html-comments.js" >&2; exit 1; }

echo "Publishing html-comments $VERSION from $DIR -> $DOMAIN"

# Forgotten version bump = pages keep the cached old file's version string and
# nothing downstream can tell old from new. Refuse before publishing.
if LIVE=$(live_version); then
  if [ "$LIVE" = "$VERSION" ]; then
    echo "HC_VERSION unchanged ($VERSION) — bump it in assets/html-comments.js before publishing" >&2
    exit 1
  fi
  echo "Live version is $LIVE; publishing $VERSION"
else
  echo "Warning: could not read the live version from https://$DOMAIN/html-comments.js (first publish, or host down) — continuing." >&2
fi

node --check "$DIR/html-comments.js"
surge "$DIR" "$DOMAIN"

# Verify the publish actually took: surge can report success while the edge
# still serves the previous file for a few seconds.
for attempt in 1 2 3 4 5 6; do
  if NOW=$(live_version) && [ "$NOW" = "$VERSION" ]; then
    echo "Live version: $NOW ✓"
    exit 0
  fi
  sleep 3
done

echo "Publish did not take: live version is '${NOW:-<unreadable>}', expected '$VERSION'" >&2
exit 1
