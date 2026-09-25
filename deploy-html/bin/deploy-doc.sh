#!/usr/bin/env bash
# Deploy one HTML document to surge.sh with the comment overlay enabled.
#
#   bin/deploy-doc.sh report.html
#   bin/deploy-doc.sh report.html --slug zcurve-accuracy-v2
#   bin/deploy-doc.sh ./site-dir --slug flora-handover
#   bin/deploy-doc.sh report.html --no-comments
#
# The source file is never modified: the overlay snippet is injected into a
# staged copy. Re-running on the same document redeploys it to the same domain
# and keeps its slug, so existing comments stay attached.
#
# Two collisions are checked before anything is uploaded, because neither is
# recoverable afterwards: deploying onto a domain that holds a different
# document destroys it, and reusing a slug points two documents at one comment
# tab (see "choose a unique data-project slug" in SKILL.md).
set -euo pipefail

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="$HOME/.claude/html-comments.config.json"

usage() {
  cat >&2 <<'EOF'
Usage: deploy-doc.sh <file.html|dir> [options]

  --slug <slug>     comment-tab slug (default: derived from the filename)
  --domain <host>   surge domain (default: <slug>.surge.sh)
  --no-comments     deploy without the comment overlay
  --force           skip the slug/domain collision checks
  --dry-run         show what would happen; upload nothing

The endpoint and asset host come from ~/.claude/html-comments.config.json.
EOF
  exit 1
}

SRC=""; SLUG=""; DOMAIN=""; COMMENTS=1; FORCE=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --slug)        SLUG="${2:-}"; shift 2 ;;
    --domain)      DOMAIN="${2:-}"; shift 2 ;;
    --no-comments) COMMENTS=0; shift ;;
    --force)       FORCE=1; shift ;;
    --dry-run)     DRY=1; shift ;;
    -h|--help)     usage ;;
    -*)            echo "Unknown option: $1" >&2; usage ;;
    *)             [ -z "$SRC" ] || { echo "Only one source may be given." >&2; usage; }
                   SRC="$1"; shift ;;
  esac
done
[ -n "$SRC" ] || usage
[ -e "$SRC" ] || { echo "No such file or directory: $SRC" >&2; exit 1; }

command -v surge   >/dev/null 2>&1 || { echo "surge CLI not found on PATH — 'npm install -g surge'." >&2; exit 1; }
command -v curl    >/dev/null 2>&1 || { echo "curl not found on PATH." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found on PATH." >&2; exit 1; }

read_cfg() {
  python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
v = cfg.get(sys.argv[2])
if not v:
    sys.exit("no \"%s\" key in %s" % (sys.argv[2], sys.argv[1]))
print(v)
' "$CFG" "$1"
}

if [ "$COMMENTS" = 1 ]; then
  [ -f "$CFG" ] || {
    echo "Missing $CFG — copy $SKILL/config.example.json there and fill it in," >&2
    echo "or deploy without the overlay using --no-comments." >&2
    exit 1
  }
  ENDPOINT=$(read_cfg endpoint)   || exit 1
  ASSETBASE=$(read_cfg assetBase) || exit 1
fi

# ---------------------------------------------------------------- source + slug
if [ -d "$SRC" ]; then
  SRCDIR="${SRC%/}"
  [ -f "$SRCDIR/index.html" ] || { echo "$SRCDIR has no index.html." >&2; exit 1; }
  DOCNAME="$(basename "$SRCDIR")"
else
  SRCDIR=""
  DOCNAME="$(basename "$SRC")"; DOCNAME="${DOCNAME%.html}"; DOCNAME="${DOCNAME%.htm}"
  # A bare index.html says nothing about the document; its folder does.
  if [ "$DOCNAME" = "index" ]; then
    DOCNAME="$(basename "$(cd "$(dirname "$SRC")" && pwd)")"
  fi
fi

if [ -z "$SLUG" ]; then
  SLUG=$(printf '%s' "$DOCNAME" | tr '[:upper:]' '[:lower:]' \
         | sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-//' -e 's/-$//' | cut -c1-90)
fi
printf '%s' "$SLUG" | grep -Eq '^[a-z0-9][a-z0-9-]{2,89}$' || {
  echo "Slug '$SLUG' is not usable: use 3-90 chars of [a-z0-9-], starting alphanumeric." >&2
  echo "Pass one explicitly with --slug." >&2
  exit 1
}
[ -n "$DOMAIN" ] || DOMAIN="$SLUG.surge.sh"
URL="https://$DOMAIN/"

# ------------------------------------------------------------ collision checks
# Both identifiers sit at the end of the document, so read the last 64 KiB
# rather than the whole body: a large page (embedded images, an embed-resources
# Quarto report) otherwise truncates or times out, and a failed read must never
# be mistaken for "nothing is deployed there".
# A suffix range larger than the file is answered with 416 by surge, so fall
# back to a plain fetch whenever the range request did not return 206.
tail_of_page() {
  local url="$1" tmp code
  tmp=$(mktemp "${TMPDIR:-/tmp}/hc-tail.XXXXXX")
  code=$(curl -sS -o "$tmp" -w '%{http_code}' --max-time 30 -r -65536 "$url" 2>/dev/null || echo 000)
  if [ "$code" != "206" ]; then
    curl -sS -o "$tmp" --max-time 30 "$url" >/dev/null 2>&1 || true
  fi
  cat "$tmp"
  rm -f "$tmp"
}

http_status() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 25 -r 0-0 "$1" 2>/dev/null || echo 000
}

# The slug of the document currently served at $DOMAIN, empty if none is
# readable. Pages deployed with --no-comments carry no overlay config, so every
# deploy also stamps an hc-doc marker.
live_slug() {
  local body found
  body=$(tail_of_page "$URL")
  found=$(printf '%s' "$body" | grep -o "hc-doc: *[a-z0-9-]\{1,\}" | head -1 | sed 's/.*: *//')
  if [ -z "$found" ]; then
    found=$(printf '%s' "$body" | grep -o "project: *'[^']*'" | head -1 | sed "s/.*'\(.*\)'/\1/")
  fi
  printf '%s' "$found"
}

if [ "$FORCE" = 1 ]; then
  echo "--force: skipping collision checks."
else
  STATUS=$(http_status "$URL")
  DOMAIN_LIVE=0
  LIVE_SLUG=""
  case "$STATUS" in
    2*|3*) DOMAIN_LIVE=1; LIVE_SLUG=$(live_slug) ;;
    000)   echo "Could not reach $URL to check what is deployed there." >&2
           echo "Re-run when the network is back, or --force to deploy blind." >&2
           exit 1 ;;
  esac

  if [ "$DOMAIN_LIVE" = 1 ] && [ -n "$LIVE_SLUG" ] && [ "$LIVE_SLUG" != "$SLUG" ]; then
    echo "$DOMAIN already serves a different document (slug '$LIVE_SLUG')." >&2
    echo "Deploying would replace it. Choose another --slug/--domain." >&2
    exit 1
  fi

  # Live, but carrying neither marker: deployed before this script existed, or by
  # something else. It cannot be shown to be the same document, so do not assume it.
  if [ "$DOMAIN_LIVE" = 1 ] && [ -z "$LIVE_SLUG" ]; then
    echo "$DOMAIN is live but carries no document marker, so this script cannot tell" >&2
    echo "whether it is the same document. Deploying would replace whatever is there." >&2
    echo "Check https://$DOMAIN/ and re-run with --force if it is yours to overwrite." >&2
    exit 1
  fi

  # Slug reuse is checked by the same script the manual embedding path uses, so
  # both routes apply one rule.
  if [ "$COMMENTS" = 1 ]; then
    CHECK="$(dirname "$SKILL")/html-comments/bin/check-slug.sh"
    [ -x "$CHECK" ] || { echo "Missing $CHECK — the html-comments skill must be installed." >&2; exit 1; }
    # A dry run checks the slug without reserving it. A real deployment claims
    # it before upload, closing the gap before the first reviewer comment.
    if [ "$DRY" = 1 ]; then
      "$CHECK" "$SLUG" --allow-existing-at "$URL" || exit 1
    else
      "$CHECK" "$SLUG" --allow-existing-at "$URL" --claim "$URL" || exit 1
    fi
  fi

  if [ "$DOMAIN_LIVE" = 1 ]; then
    echo "Updating existing deployment at $URL (slug '$SLUG')."
  fi
fi

# --------------------------------------------------------------------- staging
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/hc-deploy.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT

if [ -n "$SRCDIR" ]; then
  cp -R "$SRCDIR/." "$STAGE/"
else
  cp "$SRC" "$STAGE/index.html"
  # A lone file carrying relative asset references arrives at the host broken.
  if grep -Eq '(src|href)="(\./)?[^"#/][^":]*"' "$STAGE/index.html" \
     && grep -Eq '(src|href)="(\./)?[^"#/][^":]*\.(css|js|png|jpe?g|svg|gif|webp|woff2?)"' "$STAGE/index.html"; then
    echo "Warning: $SRC references relative assets. Deploying the file alone will break them —" >&2
    echo "         pass its directory instead if the page is not self-contained." >&2
  fi
fi
DOC="$STAGE/index.html"

# Identifies the deployed document on later runs, including without the overlay.
if ! grep -q "hc-doc: $SLUG" "$DOC"; then
  printf '<!-- hc-doc: %s -->\n' "$SLUG" >> "$DOC"
fi

# ----------------------------------------------------------- overlay injection
if [ "$COMMENTS" = 1 ]; then
  EXISTING=$(grep -o "project: *'[^']*'" "$DOC" | head -1 | sed "s/.*'\(.*\)'/\1/" || true)
  if [ -n "$EXISTING" ]; then
    if [ "$EXISTING" = "UNIQUE-PROJECT-SLUG" ]; then
      echo "The page carries the placeholder slug; replacing it with '$SLUG'."
      python3 - "$DOC" "$SLUG" <<'PY'
import sys
p, slug = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read().replace("UNIQUE-PROJECT-SLUG", slug)
open(p, "w", encoding="utf-8").write(s)
PY
    elif [ "$EXISTING" != "$SLUG" ]; then
      echo "The page already embeds slug '$EXISTING' but this deploy uses '$SLUG'." >&2
      echo "Fix the page's snippet or pass --slug $EXISTING." >&2
      exit 1
    else
      echo "Overlay already embedded with slug '$SLUG'; leaving it as is."
    fi
  else
    python3 - "$DOC" "$ENDPOINT" "$ASSETBASE" "$SLUG" <<'PY'
import sys
path, endpoint, base, slug = sys.argv[1:5]
snippet = """<script>
(function () {
  window.HC_CONFIG = {
    endpoint: '%s',
    project: '%s'
  };
  var BASE = '%s';
  var link = document.createElement('link');
  link.rel = 'stylesheet'; link.href = BASE + 'html-comments.css';
  document.head.appendChild(link);
  var s = document.createElement('script');
  s.src = BASE + 'html-comments.js';
  document.head.appendChild(s);
})();
</script>
""" % (endpoint, slug, base)

html = open(path, encoding="utf-8").read()
i = html.lower().rfind("</body>")
if i == -1:
    html = html + "\n" + snippet
else:
    html = html[:i] + snippet + html[i:]
open(path, "w", encoding="utf-8").write(html)
PY
    echo "Injected the comment overlay (slug '$SLUG')."
  fi
fi

# ---------------------------------------------------------------------- deploy
if [ "$DRY" = 1 ]; then
  echo "--dry-run: would deploy $(du -sh "$STAGE" | cut -f1) to $DOMAIN"
  echo "           slug '$SLUG', comments $([ "$COMMENTS" = 1 ] && echo on || echo off)"
  exit 0
fi

surge "$STAGE" "$DOMAIN"

# surge can report success while the edge still serves the previous file.
for attempt in 1 2 3 4 5 6; do
  BODY=$(tail_of_page "$URL")
  if printf '%s' "$BODY" | grep -q "hc-doc: *$SLUG"; then
    if [ "$COMMENTS" = 0 ] || printf '%s' "$BODY" | grep -q "project: *'$SLUG'"; then
      echo
      echo "Live: $URL"
      [ "$COMMENTS" = 1 ] && echo "Comments: $ENDPOINT?action=rows&project=$SLUG"
      echo "Remove when done: surge teardown $DOMAIN"
      exit 0
    fi
  fi
  sleep 3
done

echo "Deployed, but $URL did not come back with the expected content — check it manually." >&2
exit 1
