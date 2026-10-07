#!/usr/bin/env bash
set -euo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
args=(); provider=cloudflare
while [ "$#" -gt 0 ]; do
  case "$1" in
    --provider) provider="${2:?Missing provider}"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
case "$provider" in
  cloudflare) exec python3 "$SKILL/bin/deploy-cloudflare.py" "${args[@]}" ;;
  surge) exec "$SKILL/bin/deploy-surge.sh" "${args[@]}" ;;
  *) echo "Unknown provider: $provider" >&2; exit 1 ;;
esac
