#!/usr/bin/env bash
# SessionStart: scrollback reminder when containment ON
set -euo pipefail
STATE="${HOME}/.statepod/omp.json"
enabled=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('enabled',False))" "$STATE" 2>/dev/null || echo False)
if [ "$enabled" = "True" ]; then
  echo "[statepod] containment ON — prefer sed/grep/head / sp ask / lean-ctx signatures" >&2
fi
exit 0
