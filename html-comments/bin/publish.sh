#!/usr/bin/env bash
# Publish the canonical html-comments assets to the asset host named in
# config.local.json (`publishTarget`), or to the host given as $1.
#
# Every report loads the overlay from that host, so this is the ONLY place a
# change to html-comments.js / .css needs to be made and shipped. A static host
# that serves `cache-control: max-age=0, must-revalidate` (surge.sh does) makes
# a publish take effect on the next page load everywhere — no report needs
# re-rendering.
set -euo pipefail

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$SKILL/assets"
CFG="$SKILL/config.local.json"

if [ $# -ge 1 ]; then
  DOMAIN="$1"
elif [ -f "$CFG" ]; then
  DOMAIN=$(python3 -c "import json; print(json.load(open('$CFG'))['publishTarget'])")
else
  echo "No config.local.json (copy config.example.json and fill it in), and no host given." >&2
  exit 1
fi

VERSION=$(grep -m1 "var HC_VERSION" "$DIR/html-comments.js" | sed "s/.*'\(.*\)'.*/\1/")
echo "Publishing html-comments $VERSION from $DIR -> $DOMAIN"

node --check "$DIR/html-comments.js"
surge "$DIR" "$DOMAIN"

echo "Live version: $(curl -s "https://$DOMAIN/html-comments.js" | grep -m1 'var HC_VERSION')"
