#!/usr/bin/env python3
"""Wait for a background job to exit, then surface a terminal notification
with the tail of its log. No windows — in-harness/terminal notification
only (machine policy).

Run with a python that can import terminal_notif (the prime-agent kernel
venv has it):
    /home/grave/.prime/agent/kernel-venv/bin/python \\
        scripts/notify_on_exit.py <pid> <logfile> ["title"]

Falls back to writing the summary to <logfile>.done (and stderr) when
terminal_notif is unavailable, so the watcher never dies silently.
"""
import os
import subprocess
import sys
import time

pid = int(sys.argv[1])
log = sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else "background job finished"

while os.path.exists(f"/proc/{pid}"):
    time.sleep(15)
time.sleep(3)  # let stdout flush

tail = ""
try:
    tail = subprocess.run(["tail", "-14", log], capture_output=True,
                          text=True).stdout
except Exception:
    pass

summary = title
lines = [l for l in tail.splitlines() if l.strip()]
if lines:
    summary = title + "\n" + "\n".join(lines[-10:])

try:
    from terminal_notif import notify
    notify(title, summary, "info")
except Exception as exc:
    try:
        with open(log + ".done", "w") as f:
            f.write(summary)
        print(f"[notify_on_exit] terminal_notif unavailable ({exc!r}); "
              f"summary written to {log}.done", file=sys.stderr)
    except Exception:
        pass
    sys.exit(1)
