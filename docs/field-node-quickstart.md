# Field Node Quick Start

One page to get a node on the mesh: start a pool, join it, share
learning and files, run the acceptance demo.

## What a node is

A node is one `harness/meshd.py` process: a C-mesh peer (Lamport
clock, dedup, default-deny allowlist) plus transports.  Nodes in a
pool exchange ops (registry learning: `reg/STRAT/*`, `reg/MODEL/*`)
and control messages (`req`/`resp` inference, `file_request`/
`file_response`, `capability_announce`, `join`).  Pools specialize;
**scoped bridges** carry only what both sides accept between pools.

## Prerequisites

- Python 3, `libmesh.so` at `~/.local/lib/libmesh.so` (shared C mesh
  base; the mesh-c skill documents the build).
- `export SWARMSTATE_LIB=$PWD/libswarmstate.so` when running the
  harness (planner/oracle layer).
- `ollama serve` + a pulled model only if the node is an inference
  provider (`--serve-model`).

## 1. Start a pool hub

```bash
python3 harness/meshd.py --name hub --port 7000 \
  --allow hub --pool-secret 's3cret' --pool-name 'ward-7'
```

`--pool-secret` turns the node into a hub: it verifies signed invites
and mints the member allowlist.  The allowlist is default-deny for
ops — members are added as they join.

## 2. Issue an invite (hand to the field operator)

```bash
python3 harness/pool.py issue --secret 's3cret' --pool ward-7 \
  --hub 127.0.0.1:7000 --ttl 3600 --qr --qr-out invite.png
```

Prints the signed invite (also as a QR PNG + ANSI).  The invite is
HMAC-signed, expiring, and carries the hub address so the joiner can
dial in.  `verify` checks one:

```bash
python3 harness/pool.py verify --secret 's3cret' --invite '<invite>'
```

## 3. Join the pool with a field node

```bash
python3 harness/meshd.py --name field-1 --port 7001 \
  --peer 127.0.0.1:7000 --allow field-1 --join '<invite>'
```

The node dials the hub, presents the invite, receives the member
list, and catches up on pool history.  Any op it publishes (registry
learning, e.g. `reg/STRAT/WRITE:DELTA=1`) now converges across the
pool — `sha256(mesh_peer_state_json())` matches on every member.

## 4. Serve and fetch files (control channel)

```bash
# archive node: serve only *.py and conf/* from a share root
python3 harness/meshd.py --name archive --port 7002 --allow archive \
  --serve-files /srv/swarm/share --file-patterns '*.py,conf/*' \
  --max-file-bytes 65536
```

Fetch from Python (or via the pool bridge relay):

```python
from meshd import MeshDaemon
node = MeshDaemon("field-1", 7001, [("127.0.0.1", 7000)],
                  allow={"field-1"}, join_invite=INVITE)
node.start()
r = node.request_file("oracle_read.py", to="archive", timeout=5)
assert r["ok"]; print(r["content"])
```

Denials always carry a reason (`path escapes the share root`, `path
not in serve patterns`, `file too large`), never a hang.  Default-deny
by construction: no `..`, no absolute paths, allowlist checked before
existence (the policy never doubles as a file oracle).

## 5. Run the acceptance demo (spec v1 §9, 5/5 criteria)

```bash
bash scripts/acceptance_demo.sh
```

Chains the five demos: pool/QR state sync, capability-routed local
inference, cross-node refusal of contradictory evidence, governance
stop button, and LoRa air-gapped convergence.  Logs land in
`/tmp/ss_acceptance/`.

Individual demos: `mesh_flip_demo.sh`, `mesh_broker_demo.sh`,
`governance_demo.sh`, `pool_qr_demo.sh`, `broker_capability_demo.sh`,
`mesh_lora_smoke.sh`, `pool_bridge_demo.sh`, `file_exchange_demo.sh`.

## meshd flags

| flag | meaning |
|---|---|
| `--name` | node id (clock origin) |
| `--port` | listen port |
| `--peer HOST:PORT` | outbound peer (repeatable) |
| `--allow a,b` | peer ids allowed to inject ops (default-deny if set) |
| `--pool-secret S` | run as pool hub with invite secret |
| `--pool-name N` | pool display name |
| `--join I` | join a pool with signed invite |
| `--serve-model M` | inference provider with local model |
| `--announce-models a,b` | capability_announce every 5 s |
| `--serve-files ROOT` | serve files from share root |
| `--file-patterns 'a,b'` | serve allowlist globs (quote them) |
| `--max-file-bytes N` | largest served file (default 65536) |
| `--lora-publish T V` | publish ops as one LoRa batch |
| `--stdio` | Tailcat/stdin-stdout transport instead of TCP |

## Health checks

- Convergence: the per-second `HASH <sha256> tail=N` line shows the
  folded state; identical hash on all members = converged.
- Registry: a runner logs `STRAT:<sig>:<strat>=<rate>/<samples>` —
  samples growing across nodes proves collective learning.
- Governance: `--governance enforce` on a runner denies governed ops
  without a valid token; every verdict lands in the JSONL audit log.
