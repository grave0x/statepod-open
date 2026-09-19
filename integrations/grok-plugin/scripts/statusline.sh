#!/usr/bin/env bash
# Optional [ui.status_line] command — compact kernel sample when containment ON.
set -euo pipefail
REPO="${STATEPOD_REPO:-$HOME/Projects/internal.source/02-tools/statepod}"
STATE="$HOME/.statepod/omp.json"
enabled=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('enabled',False))" "$STATE" 2>/dev/null || echo False)
if [ "$enabled" != "True" ]; then
  exit 0
fi
# Prefer a one-liner: ON + kernel status (no multi-line dump into the row)
out=$(python3 "$REPO/integrations/grok-plugin/scripts/statepod_cli.py" status 2>/dev/null | head -2 | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')
# Keep under ~80 cols for the status row
printf '%.80s\n' "${out:-ss:ON}"
