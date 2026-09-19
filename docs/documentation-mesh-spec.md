## StatePod Mesh Integration Specification

**Version:** 1.0 (Draft)  
**Author:** @grave0x  
**Date:** August 2026  
**Status:** Design / Roadmap  
**Dependency:** [agent-mesh](https://github.com/grave0x/agent-mesh) (600‑line peer‑to‑peer communication layer)

> **Document status:** end-state design and building decisions. `README.md`
> documents what is actually implemented today; nothing in this spec exists
> in the code yet — it is the roadmap for turning StatePod into a
> distributed personal swarm.

---

### 1. Executive Summary

StatePod currently operates as a single‑device agent harness: a local C kernel, an orchestrator, and a registry. The Mesh Integration Specification extends StatePod into a **personal distributed swarm** by connecting multiple StatePod instances over the existing `agent-mesh` communication layer. Each device runs its own kernel and local model, but the mesh enables shared inference, state synchronization, registry aggregation, and LoRA distribution—all without centralized servers.

**Key Benefits:**
- **Shared inference**: Offload LLM requests to the most capable or least loaded device.
- **State sync**: Keep `RepoState` consistent across laptops, phones, and workstations using only local diffs.
- **Collective learning**: Aggregate performance and feedback registries across devices, so the swarm improves faster than any single node.
- **Resource‑aware routing**: Use sysinfo from all nodes to decide where to execute a plan or run a model.
- **Privacy & resilience**: No cloud dependency; data stays inside the personal mesh. If one device fails, others continue.

**Honest Claim:** This specification defines a peer‑to‑peer extension to StatePod, not a cloud service. It assumes the user owns all participating devices and that the mesh operates within a trusted personal network.

---

### 2. Core Philosophy

> The kernel contains the context, the mesh connects the bodies.

| Principle | Implication |
|-----------|-------------|
| **Local first, mesh second** | Each node remains fully functional offline. Mesh features are additive, not required. |
| **Minimal trust, maximal encryption** | All mesh traffic is encrypted end‑to‑end. Nodes must authenticate before joining. |
| **State is source of truth** | The mesh shares diffs and events, not full snapshots. Each node's kernel remains authoritative for its own local state. |
| **The registry is a distributed memory** | Success rates, timings, and feedback aggregate across nodes to produce better strategy decisions. |
| **Routing is mechanical** | The kernel decides where to send an inference request based on resource snapshots and historical latencies—not the LLM. |

---

### 3. Problem Statement

A single‑device StatePod instance already reduces token usage and improves privacy. However, users often have multiple devices:
- A powerful desktop for heavy coding.
- A laptop for mobile work.
- A phone for quick checks or voice commands.

Without a mesh, each device is an isolated silo. The registry does not share learnings, state drifts across devices, and expensive models or compute sit idle while another device struggles. Existing solutions rely on cloud sync, which violates the privacy‑first principle.

**Missing Component** | **Mesh Provides**  
---|---
Cross‑device inference | Route LLM requests to the best available node.  
State continuity | Sync repo changes via event logs and state hashes.  
Collective learning | Aggregate `PerfEntry` and feedback records.  
Resource pooling | Share CPU/GPU/RAM capabilities.  
Peer‑to‑peer LoRA sharing | Exchange fine‑tuned adapters without a central hub.  

---

### 4. Architecture Overview

The mesh layer sits **below the harness** and **above the transport** provided by `agent-mesh`. It consists of:

1. **Mesh Manager** – Handles peer discovery, authentication, and connection lifecycle.
2. **Message Router** – Serializes, encrypts, and routes StatePod‑specific messages.
3. **State Sync Engine** – Propagates repo state diffs using the kernel's event log.
4. **Registry Aggregator** – Merges performance and feedback data from peers.
5. **Inference Broker** – Receives inference requests and dispatches them to local or remote orchestrators.
6. **LoRA Exchange** – Securely shares LoRA adapters between trusted nodes.

```
┌──────────────────────────────────────────────┐
│               StatePod Harness              │
│         (Orchestrator, Planner, CLI)          │
└──────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│          Mesh Manager (agent-mesh)            │
│  Discovery │ Authentication │ Encryption │    │
└──────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│  Message Router & Protocol Handlers           │
│  State Sync │ Registry │ Inference │ LoRA     │
└──────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│          Local Kernel & State                 │
└──────────────────────────────────────────────┘
```

---

### 5. Mesh Topology

The default topology is **full mesh within a personal group**—every node knows every other node. For larger deployments (e.g., a small team), a **gossip‑based partial mesh** can be used, but the initial spec targets a small, fully connected personal swarm (≤ 10 devices).

Each node is identified by a stable, self‑generated `NodeID` (UUID) and an Ed25519 public key. Devices join the mesh via a shared **secret phrase** or an explicit pairing QR code.

---

### 6. Message Protocol

All messages are JSON objects wrapped in an encrypted envelope.

```json
{
  "version": 1,
  "type": "inference_request",
  "id": "a1b2c3...",
  "timestamp": 1724800000,
  "sender": "node-1",
  "payload": { ... }
}
```

#### 6.1 Supported Message Types

| Type | Direction | Payload |
|------|-----------|---------|
| `hello` | Any → Any | NodeID, capabilities (CPU, GPU, models), state hash |
| `state_diff` | Any → Any | Repo‑relative path, diff content, parent state hash |
| `inference_request` | Any → Any | Task description, context strategy, plan schema version |
| `inference_response` | Any → Any | Plan JSON or error, token usage, duration |
| `registry_update` | Any → Any | Aggregated `PerfEntry` records (signature, strategy, timings, ok/fb counts) |
| `lora_share` | Any → Any | LoRA adapter ID, base model, weights hash, metadata |
| `resource_snapshot` | Any → Any | Memory, CPU, battery, current load (optional heartbeat) |

#### 6.2 Envelope Encryption

- Every payload is encrypted with **AES‑256‑GCM** using a per‑message ephemeral key.
- The ephemeral key is exchanged using **X25519** key agreement between sender and receiver.
- Authentication is via **Ed25519** signatures on the envelope metadata (sender, timestamp, id) to prevent replay.

---

### 7. Core Services

#### 7.1 State Sync

State sync uses the kernel's existing event log. Today each entry is a
JSON line with `serial`, `ts`, `op`, `path`, `ok`, `note`, and `res`
(resource tag). The hash chain fields below (`content_hash`, `prev_hash`)
are a **planned Phase 2 kernel extension** — they do not exist in the
current event log (the kernel computes file content hashes and a state hash,
but does not yet record them per event). When a node detects a state change
(e.g., after a `WRITE` op), it broadcasts a `state_diff` to all peers.

**Conflict Resolution:**
- If two nodes modify the same file concurrently, the node with the **older modification timestamp** is rejected, and the other node's change is accepted.
- The kernel's checkpoint mechanism allows rollback if needed.
- `RepoState` remains authoritative on each node; the mesh only propagates changes.

**Optimization:** Diffs are tiny (typically < 1 KB) because only changed file paths and hashes are sent, not full file contents. Full file transfer happens only on explicit request (`file_request` message type, to be added later).

#### 7.2 Shared Inference

The Inference Broker exposes a single virtual endpoint to the orchestrator: `mesh://inference`. When the local orchestrator needs a plan (or an escalation), it sends an `inference_request` with:
- Task description (the same 62‑byte prompt).
- Context strategy (`DELTA`, `TARGETED`, `FULL`, `SYMBOLIC`).
- Preference hints: `min_quality`, `max_latency`, `preferred_model`.

The broker then:
1. Collects `resource_snapshot` and `hello` capabilities from peers.
2. Scores each candidate based on:
   - Historical success rate for the task signature (from local registry + aggregated peers).
   - Current memory/CPU availability.
   - Model capability match.
   - Network latency (if measurable).
3. Routes the request to the highest‑scoring node.
4. Returns the first valid response (or falls back to next node on timeout).

**Local fallback:** If no peer is available or fails, the request is handled locally by the local orchestrator. This preserves offline functionality.

#### 7.3 Registry Aggregation

Each node maintains its own `PerfEntry` list. Periodically (or on demand), nodes broadcast `registry_update` messages containing entries they have not yet shared. The receiving node merges them into its local registry.

**Aggregation Rules:**
- Entries are keyed by `signature|kernel|strategy`. Counts and timings are summed or averaged.
- Feedback counts (`fb_ok`, `fb_count`) are also aggregated.
- A simple **FedAvg‑like weighting** can be applied, but v1 uses raw accumulation.

This means the confidence gate on any node can make decisions informed by the collective experience of the swarm.

#### 7.4 LoRA Exchange

LoRA adapters are small (10 MB) and can be shared directly between peers. The `lora_share` message includes the adapter ID, base model, SHA‑256 hash, and optional metadata. Nodes can request a missing adapter using a `lora_request` message (not yet in the message table but easily added).

**Security:** Only devices that have been authenticated and explicitly trusted can exchange LoRAs. The user controls which adapters are shared and with whom.

---

### 8. Integration Points with Existing StatePod

#### 8.1 Kernel Changes

The C kernel needs minimal changes:
- A new `SP_OP_MESH_SEND` operation could be added, but for v1, mesh interactions happen in the harness using the existing Python bindings. The kernel remains mesh‑agnostic.
- The one real kernel change for state sync: extend the event log with
  `content_hash` + `prev_hash` per entry (the hash chain described in §7.1).
  That is a small, additive change to `event_log()` in `kernel.c`; the
  existing fields (`serial`, `ts`, `op`, `path`, `ok`, `note`, `res`)
  stay unchanged so SNAP/event-log loaders remain backward compatible.

#### 8.2 Harness Changes

- Add a `MeshManager` class in Python that wraps `agent-mesh`.
- The orchestrator's `ask()` method gains a new backend option: `"mesh"`.
- When `backend == "mesh"`, plan generation is delegated to the Inference Broker instead of a direct model call.
- The task suite can optionally run in "mesh mode" to benchmark cross‑device performance.

#### 8.3 Registry Changes

- Extend the registry to accept aggregated entries from peers.
- Add a background thread that periodically broadcasts local registry updates and processes incoming ones.
- The confidence gate already reads from the registry; no changes needed to its logic.

#### 8.4 Security & Trust

- All mesh nodes are assumed to be owned by the same user or a trusted group. Authentication via a shared secret or QR pairing.
- All traffic is encrypted and signed; no plaintext code or prompts leave the device.
- The mesh can be disabled entirely with a single configuration flag.

---

### 9. Performance Expectations

| Metric | Target |
|--------|--------|
| State diff latency (small repo) | < 10 ms per diff |
| Registry update size | < 5 KB per 100 entries |
| Inference routing overhead | < 50 ms added latency |
| LoRA transfer time | < 2 s on LAN, < 10 s on Wi‑Fi |
| Battery impact (phone) | < 5% per hour when idle |

These are estimates for a 3–5 node home mesh. Actual performance will depend on the `agent-mesh` implementation.

---

### 10. Roadmap

#### Phase 1: Mesh Core
- Integrate `agent-mesh` into the harness as a background service.
- Implement node discovery, authentication, and encrypted transport.
- Establish a persistent connection between two StatePod instances on the same LAN.

#### Phase 2: State Sync
- Broadcast state diffs on file changes.
- Resolve conflicts using timestamp ordering.
- Test state consistency across two devices after a series of file writes.

#### Phase 3: Shared Inference
- Implement the Inference Broker and routing logic.
- Add `mesh` backend to the orchestrator.
- Benchmark cross‑device inference latency and success rate vs. local‑only.

#### Phase 4: Registry Aggregation
- Add registry update message handlers and merging logic.
- Validate that aggregated success rates improve confidence‑gate decisions.

#### Phase 5: LoRA Exchange & Advanced Routing
- Implement LoRA share/request messages.
- Add more sophisticated routing (e.g., cost‑benefit based on battery and model availability).
- Explore opportunistic mesh formation when devices appear on the same Wi‑Fi.

---

### 11. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Network latency makes remote inference slower than local | Fallback to local if latency exceeds threshold; registry learns per‑node latency. |
| State sync conflicts corrupt repo | Use content hashes and parent hashes; allow rollback via kernel checkpoints. |
| Security breach if a node is compromised | End‑to‑end encryption and per‑node keys; revoke trust by removing node from allowlist. |
| Battery drain on mobile devices | Mesh activity only when app is active or on Wi‑Fi; aggressive sleep mode. |
| `agent-mesh` API instability | Since it's a 600‑line layer, we can fork and adapt as needed. |

---

### 12. Open Questions

- Should state sync be push‑based (immediate broadcast) or pull‑based (periodic polling)? Push is faster but may increase network traffic; pull is simpler but laggy.
- How to handle nodes that are intermittently offline? Queued diff messages with sequence numbers, applied on reconnect.
- Can the mesh extend to untrusted devices (e.g., a friend's computer) with limited permissions? Future extension with capability‑based security.

---

### 13. Conclusion

The StatePod Mesh Integration Specification turns a single‑device, context‑containing agent into a **distributed personal swarm**. By leveraging the existing `agent-mesh` layer, StatePod gains shared inference, state continuity, and collective learning—all without sacrificing privacy or requiring cloud infrastructure. This is the natural evolution of the "kernel contains context" philosophy: now the mesh connects the bodies, and the swarm becomes the brain.
