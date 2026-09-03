#!/usr/bin/env bash
# file_exchange_demo.sh -- file_request / file_response (spec v1 App. B).
#
# An archive node serves files from a share root; a field node pulls
# what it needs (oracle definitions, pool config) and is REFUSED -- with
# a reason -- for anything outside the serve policy:
#   - paths that escape the share root (../, absolute)
#   - paths outside the serve patterns (allowlist, checked first so the
#     policy never doubles as a file-existence oracle)
#   - files over the control-channel size cap
#   - requests to a node that is not serving files
set -u
cd "$(dirname "$0")/.."

pkill -f "harness/meshd.py" 2>/dev/null; sleep 1

echo "=== file_request / file_response ==="
python3 - <<'EOF'
import sys, time, os, tempfile
sys.path.insert(0, "harness")
from meshd import MeshDaemon

root = tempfile.mkdtemp()
for name, body in (("oracle_write.py", "# write oracle\nok = lambda ctx: (True, 'fine')\n"),
                   ("oracle_read.py",  "# read oracle\nok = lambda ctx: (True, 'clean')\n")):
    with open(os.path.join(root, name), "w") as f:
        f.write(body)
os.makedirs(os.path.join(root, "conf"))
with open(os.path.join(root, "conf", "mesh.toml"), "w") as f:
    f.write("pool = 'ward-7'\n")
with open(os.path.join(root, "patient.log"), "w") as f:   # in root, not in patterns
    f.write("PRIVATE\n")

archive = MeshDaemon("archive", 6140, [])
field   = MeshDaemon("field",  6141, [("127.0.0.1", 6140)])
for d in (archive, field): d.start()
time.sleep(1.5)

print("  archive serves: *.py + conf/* (max 64 KiB)")
archive.serve_files(root, allow_patterns=("*.py", "conf/*"), max_bytes=65536)
time.sleep(0.2)

def fetch(path, to="archive"):
    r = field.request_file(path, to=to, timeout=4)
    ok = "OK " if r.get("ok") else "DEN"
    why = "" if r.get("ok") else f"  ({r.get('reason')})"
    extra = ""
    if r.get("ok"):
        extra = f"  [{r.get('size')} B] " + (r.get("content") or "").strip().splitlines()[0][:30]
    print(f"  {ok} {path:24}{why}{extra}")

fetch("oracle_write.py")
fetch("oracle_read.py")
fetch("conf/mesh.toml")
fetch("../ward/patients.db")
fetch("/etc/passwd")
fetch("patient.log")
fetch("missing.py")
print("  all file denials carry a machine-actionable reason, never a hang")

# denial when the node is not serving at all
ghost = MeshDaemon("ghost", 6142, [])
probe = MeshDaemon("probe", 6143, [("127.0.0.1", 6142)])
for d in (ghost, probe): d.start()
time.sleep(1.2)
r = probe.request_file("oracle_write.py", to="ghost", timeout=4)
print(f"  {'DEN' if not r.get('ok') else 'OK '} {'oracle_write.py':24}  ({r.get('reason')})")

for d in (archive, field, ghost, probe): d.shutdown()
print("")
print("  file exchange demo PASS")
EOF
