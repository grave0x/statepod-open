#!/usr/bin/env bash
# SessionStart: scrollback reminder when containment ON.
# Note: Grok ignores SessionStart stdout for *model* context; PreToolUse
# guidance_once() injects the model-visible note on the first tool call.
set -euo pipefail
STATE="${HOME}/.statepod/omp.json"
enabled=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('enabled',False))" "$STATE" 2>/dev/null || echo False)
if [ "$enabled" = "True" ]; then
  # stderr reaches the hooks UI / scrollback annotation channel
  echo "[statepod] containment ON — PreToolUse caps dumps; PostToolUse archives oversized results; prefer sed/grep/head / sp ask / lean-ctx signatures." >&2
fi
exit 0
