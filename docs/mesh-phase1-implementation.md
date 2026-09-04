# Mesh Phase 1 — Engineering Reconciliation & Implementation Plan

Status: ACTIVE (GAPS priority #9)
Spec: [mesh-spec-v2.md](mesh-spec-v2.md) (product vision)
Constraint: engineering rules — **reuse the shared C mesh base**
(`~/Projects/02-tools/mesh`, installed `~/.local/bin/mesh`,
`~/.local/lib/libmesh.a`, `~/.local/include/mesh.h`), do NOT write a second mesh.

## 1. Where the v2 spec and the codebase agree

- **Message sizes.** The spec's core claim — 62-byte prompts, sub-KB messages —
  is *measured*, not aspirational. This session logged `prompt_bytes` in the
  140–460 range and 1–2 KB turns. LoRa viability is credible.
- **Registry aggregation = collective memory.** The `MODEL:q:<qsig>:<model>`,
  `STRAT:<sig>:<strat>:<model>`, and `MODEL:<sig>:<model>` keys shipped this
  session are exactly the entries the spec's §6.3 wants to merge across nodes.
  The `:<model>` dimension the spec calls out ("and soon `:model`") is already
  done (`761c672`).
- **Inference broker = rate-based router.** `pick_model_rates` is the
  single-node broker. The mesh broker is the same scoring loop with *node
  capability snapshots* (`capability_announce`) instead of a local `models`
  list. The fallback-to-local path already exists.
- **State sync = kernel event log.** `event_log()` already records
  `serial|ts|op|path|ok|note|res`. The spec's `content_hash`/`prev_hash` chain
  is the additive kernel change documented in the v1 spec §8.1.
- **42/42 semantic baseline.** Clean yardstick: mesh mode must not regress it.

## 2. Where the v2 spec must bend to the constraints

1. **Transport is a callback, not a protocol.** The C mesh exposes
   `mesh_send_fn` (broadcast callback) + `mesh_peer_apply_json` (ingest). Every
   transport in the v2 spec — Tailcat, LoRa, mDNS/QR pairing — is a *shim*
   that moves the same JSON op dicts between these two hooks. Tailcat is the
   primary shim for IP networks (Phase 2+); it is **not installed** on this
   machine yet, so Phase 1 uses **plaintext-LAN TCP + node-id allowlist**
   (GAPS #9 acceptance) and swaps the socket for a Tailcat tunnel later.
2. **Wire format stays JSON op dicts** (`op|target|value|clock{counter,origin}`),
   matching `agent-mesh`. The spec's CBOR is a *LoRa-only compression* concern,
   not a v1 protocol change.
3. **No new message schema.** The v2 §5 message types (heartbeat,
   inference_request, state_diff, registry_update, ...) are *application
   payloads* carried as `mesh_op.value` strings on top of the op wire format —
   or, better, as direct `set`/`append` ops on a typed target namespace
   (`reg/STRAT/<sig>/<strat>`). The mesh itself stays a dumb, stateless,
   Lamport-ordered op store.
4. **The C mesh CLI is local-only today** (`mesh peer` is interactive, no
   networking). The C *library* is embeddable and network-ready (it just
   expects the harness to provide the transport). Phase 1 builds that harness
   transport shim, not mesh code.

## 3. Phase 1 target (GAPS #9 acceptance, unchanged)

> Two SwarmState harnesses exchange ≥2 registry STRAT samples each way and
> converge on a shared state hash. Plaintext-LAN + node-id allowlist first.

Concretely: node A publishes 2 `reg/STRAT/...` ops, node B publishes 2, and
after the exchange both nodes fold the *same* op set, so
`sha256(mesh_peer_state_json())` is identical on both sides.

## 4. Build plan

| # | Piece | File | Status |
|---|-------|------|--------|
| 1 | ctypes binding to `libmesh` | `harness/meshbridge.py` | DONE |
| 2 | mesh daemon (TCP + stdio transports, relay, catch-up, `--allow`) | `harness/meshd.py` | DONE |
| 3 | two-node localhost smoke test | `scripts/mesh_smoke.sh` | DONE |
| 4 | Orchestrator publishes STRAT feedback as mesh ops | `harness/orchestrator.py` + `scripts/run_named.py` (`--mesh-*`) | DONE (Phase 2) |
| 5 | Tailcat transport shim (swap socket for tunnel) | `scripts/mesh_tailcat.py` + `scripts/mesh_tailcat_smoke.sh` | DONE (Phase 2) |
| 6 | LoRa adapter + CBOR framing | separate | Phase 3 |

## 5b. Phase 2 notes (2026-08-28)

- **Live registry learning**: `Orchestrator.feedback()` now publishes the
  STRAT sample (`reg/STRAT/<sig>/<strat>`) and the query-keyed model rate
  (`reg/MODEL/<qsig>/<model>`) as mesh ops. Two in-process nodes fold the
  same memory (test `test_mesh_live_feedback_converges`).
- **stdio transport**: `meshd --stdio` treats stdin/stdout as one peer link
  (op frames on stdout, diagnostics on stderr). `StdioStream.recv` uses
  `read1` (recv semantics), not `read(n)` (which blocks for a full buffer).
- **Tailcat gotcha**: raw `tailcat` stdio is ONE-way (client->server). The
  full-duplex path is `tailcat --serve=<port>` (server TCP forward) + client
  stdio. Hence `mesh_tailcat.py` is asymmetric: server = TCP meshd behind
  `--serve`, client = stdio meshd. The tunnel rendezvous via the public DERP
  relay was verified on this machine.
- **Late-tunnel race**: the transport re-sends its op history on the first
  inbound op (`_send_catchup` + `_handle_conn` `first` flag) because lazy
  transports come up AFTER the startup catch-up and drop pre-connection
  writes. Dedup by Lamport clock makes re-send always safe.

## 5. Phase 1 semantics

- **Daemon** runs one C-mesh peer (peer id = `--name`), listens on
  `--port`, and connects to each `--peer host:port`.
- **Publish**: reads `target<TAB>value` lines from stdin (or a JSONL file);
  each line becomes a local `append` op via `mesh_peer_mutate`, which the C
  mesh applies and broadcasts through the send callback to every peer socket.
- **Ingest**: each inbound JSON op goes through `mesh_peer_apply_json`
  (Lamport-dedup); newly-accepted ops are appended to the local history and
  forwarded to the other peers (relay).
- **Catch-up**: on a new connection, each side first sends its recent op
  history (bounded ring), then live ops. The mesh dedups, so convergence is
  by construction: every node that saw all ops folds the same state.
- **Allowlist**: `--allow a,b` keeps only ops whose `clock.origin` is listed;
  everything else is dropped before apply.
- **Convergence proof**: `--hash-interval 1` prints `sha256(state_json)` each
  second; the smoke test asserts A.hash == B.hash after the exchange.

## 6. Security posture (v1, matches spec §7 "default deny")

- No encryption yet (plaintext-LAN is an explicit GAPS Phase 1 concession).
  Tailcat (Phase 2) brings WireGuard E2E encryption + key auth exactly as the
  spec §4.1/§7.2 describes.
- `--allow` node-id filtering is the v1 trust boundary (the spec's allowlist
  concept, implemented at the op level rather than the TLS level).
- No open discovery: peers only connect to explicitly named hosts.

## 7. Open questions (from v2 §13), decided for v1

1. **Discovery scope** → *opt-in* for v1. mDNS/QR pairing is Phase 3; nothing
   advertises by default. "Default deny" wins over convenience.
2. **Mesh size** → the fresh-window replay (last-N, `task_suite.py`) already
   bounds registry noise; the op ring is a fixed power-of-2 (4096 default).
   Aggregation stays per-signature-keyed, so 10–50 nodes are fine.
3. **Privacy** → *aggregate-by-default*: Phase 1 ships only success/timing
   flags (`reg/STRAT/<sig>/<strat>`), never task content or file data.
4. **LoRa licensing** → defer (Phase 3); document the caveat at that point.
