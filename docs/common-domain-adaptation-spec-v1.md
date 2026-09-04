# SwarmState Common Domain Adaptation Spec v1.0

**Status:** Distilled from live design discussions  
**Purpose:** Provide a reusable, domain-agnostic framework for extending SwarmState into any operational environment.  
**Audience:** System architects, domain specialists, and future contributors.

---

## 1. Core Invariants

These components do not change between domains. They are the foundation upon which all adaptations are built.

- **Kernel (C)** — owns local state, enforces mechanical allowlists, validates plans, and executes ops deterministically. Does not trust the model.
- **Registry** — persistent memory of success/failure per task signature, strategy, model, and resource context. Learns from semantic feedback.
- **Mesh** — encrypted, peer-to-peer transport for state diffs, registry updates, and inference requests. Default-deny, explicit-accept.
- **Oracles** — domain-specific judges that determine semantic success or failure of an executed plan.
- **Ops** — the only actions available to the system. Strictly validated, typed, and limited. The model cannot invent new ops.

**Containment principle:** The model sees only summaries, never raw state. It reasons over structured plans, not arbitrary text.

---

## 2. Domain Adaptation Process

To bring SwarmState to a new domain, follow these steps:

1. **Identify repeatable tasks**  
   List the top 5–20 actions a worker performs daily. These become task signatures.

2. **Define the state model**  
   What data must the kernel hold? Examples: patient vitals, sensor readings, vehicle telemetry, field reports.

3. **Specify the op library**  
   For each task, define the minimal set of ops required. Prefer read-only ops first; add writes/controls only with governance.

4. **Write semantic oracles**  
   For each op or task family, create a deterministic check that returns `(ok, reason)` based on real-world outcome.

5. **Design the mesh topology and pools**  
   Which devices are nodes? What trust boundaries exist? Who can bridge with whom?

6. **Add governance hooks for high-risk actions**  
   If any op can cause physical or financial harm, require supervisor auth and a recorded reason.

7. **Build a minimal demo (3 nodes recommended)**  
   Show one real task, one shared state update, and one cross-node learning event.

---

## 3. Common Op Categories

Most domains can be served by a small set of op families.

| Category | Typical Ops | Notes |
|----------|-------------|-------|
| **Read** | `READ`, `GREP`, `AST_QUERY`, `SYMBOL_SUMMARY` | Always safe. The core of context serving. |
| **Write** | `WRITE`, `APPEND`, `CONFIG_UPDATE` | Requires blind-write guard and often file-content validation. |
| **Execute** | `EXECUTE`, `RUN_SCRIPT`, `HTTP_FETCH` | Most dangerous. Requires binary allowlist, argument validation, and possibly sandbox. |
| **Sense** | `READ_SENSOR`, `QUERY_DEVICE`, `GET_TELEMETRY` | Domain-specific but read-only. |
| **Actuate** | `SET_OUTPUT`, `CONTROL_VEHICLE`, `ADJUST_PARAMETER` | High risk. Always require auth + reason. |
| **Communicate** | `MESH_SEND`, `BRIDGE_PUBLISH`, `NOTIFY_USER` | Enables coordination. Must be scoped by pool. |

**Rule of thumb:** Start with only `READ` and `GREP`. Add writes when necessary. Add actuation only after proving the system is trustworthy.

---

## 4. Oracle Design Patterns

Oracles turn “did it run” into “did it work.” They are the key to self-improvement.

### Pattern 1: Deterministic Check
- Compare output against a known expected value.
- Example: after `WRITE` replacing variable `x` with `count`, grep for `x`; it must be absent.

### Pattern 2: Survivor / Structural Check
- Ensure important content still exists after an edit.
- Example: after removing an unused function, confirm that other functions remain unchanged.

### Pattern 3: Threshold / Anomaly Check
- Compare sensor data to learned baseline or fixed limits.
- Example: if temperature > 100°C, flag overheating.

### Pattern 4: Human Feedback
- Use explicit user confirmation (`y/n`) or retroactive marking.
- Example: “Did the system correctly route the truck?” Store the result.

**Oracle output format:**
```
(ok: bool, reason: str)
```
The reason must be model-actionable, e.g., “File still contains old variable name `x`; replacement content was not written.”

---

## 5. Pool and Federation Model

SwarmState scales by keeping trust local and shared knowledge scoped.

- **Pool** — a set of devices that trust each other fully. Membership is explicit (QR, token, admin).
- **Bridge** — a mutually accepted connection between two pools. Bridges are scoped:
  - Allowed message types
  - Allowed task signatures
  - Time window
  - Data minimization rules

**Default posture:** No pool may reach another without both sides accepting.  
**Result:** Specialized registries remain clean; cross-domain noise is minimized.

### Federation patterns
| Use Case | Pools involved | Shared via bridge |
|----------|----------------|-------------------|
| Emergency response | CFS, SES, Hospital, Red Cross | Hazard reports, route status, bed availability |
| Enterprise | Maintenance, Safety, Security | Anomaly alerts, safety incidents, access logs |
| Personal | Home, Vehicle, Health | Calendar state, location, health alerts |

---

## 6. Governance for High-Risk Actions

For any op that can harm people, equipment, or data, enforce:

- **Schema validation** — the op must match a predefined shape.
- **Auth token** — signed by a supervisor or role with authority.
- **Recorded reason** — a human-readable justification stored in the event log.
- **Staged response** — prefer alerts first, then limits, then full stop.
- **Human override** — always possible, always logged.

**Example governance op:**
```json
{
  "type": "CONTROL_VEHICLE",
  "action": "limit_speed",
  "target_speed": 40,
  "auth_token": "sig...",
  "reason": "Engine overheating, driver unresponsive"
}
```

The kernel rejects any high-risk op missing auth or reason. No exceptions.

---

## 7. Safety and Containment Principles

- **The model is never trusted** — it only produces plans; the kernel validates.
- **Mechanical constraints** — allowlists, path containment, symlink resolution, `O_NOFOLLOW`.
- **Blind-write guard** — if a write plan would modify unseen files, first include file content in context.
- **Idempotence** — mesh operations use Lamport clocks and dedup so re-sends are safe.
- **Fresh-window registry** — replay only recent feedback to avoid stale samples.
- **Era signals** — record whether guard/oracle-retry was active, to separate pre/post-fix behavior.

---

## 8. Deployment Patterns

### Single Node
- Kernel + local model + registry.
- Suitable for personal use, small farms, single workstations.

### Small Mesh (2–10 nodes)
- One or more inference hubs, thin clients, and sensors.
- Shared registry via mesh bridge.
- Example: CFS brigade with 3 trucks and a base station.

### Federation of Meshes
- Multiple pools with scoped bridges.
- Each pool remains specialized; bridges carry only essential summaries.
- Example: hospital + CFS + SES during a disaster.

### Air-Gapped / Offline
- No internet, no cloud. Tailcat or LoRa for transport.
- Registry and models live entirely on device.
- Suitable for remote areas, defence, secure facilities.

---

## 9. Demo Templates and Acceptance Criteria

For any new domain, build a 3-node demo that proves:

1. **State sync** — a change on one node appears on all others.
2. **Local inference** — a small model produces a useful semantic summary from raw data.
3. **Cross-node learning** — Node A learns a bad lesson; Node B refuses to repeat it (or vice versa).
4. **Governance** — a high-risk action requires auth and reason, and is logged.

**Acceptance criteria:**
- All nodes converge to the same state hash.
- No raw data leaves the pool unless explicitly allowed.
- The registry learns from semantic oracles, not just exit codes.
- A layperson can join a node by scanning a QR code and pressing one button.

---

## 10. Example Adaptation Matrix

| Domain | State | Ops | Oracle | Pool/Bridge |
|--------|-------|-----|--------|-------------|
| Code assistant | Repo files, AST | READ, WRITE, GREP, AST_QUERY | Tests, linters, expected diffs | Single dev or team mesh |
| Emergency (CFS/SES) | Incident map, crew locations | SENSE, REPORT, NOTIFY | Ground truth reports, GPS | CFS pool ↔ SES pool ↔ Hospital pool |
| Hospital ward | Patient vitals, bed state | READ_SENSOR, ALERT, CONTROL_PUMP | Medical guidelines, clinician feedback | Ward pool ↔ ED pool ↔ Pharmacy |
| Agriculture | Soil, weather, animal tags | READ_SENSOR, FLAG_ANOMALY | Field observations, yields | Farm pool (local only) |
| Industrial | Machine telemetry, PLC registers | READ_TELEMETRY, SET_PARAMETER | Downtime logs, maintenance records | Plant pool ↔ Safety pool |
| Fleet logistics | OBD-II data, GPS, driver logs | READ_TELEMETRY, CONTROL_SPEED | Accident reports, fuel data | Fleet pool ↔ Depot pool |

---

## 11. Appendices

### A. Registry Key Schema (current)
- `STRAT:<plan_sig>:<strategy>` — strategy success rate.
- `STRAT:<plan_sig>:<strategy>:<model>` — per-model strategy success.
- `MODEL:q:<query_sig>:<model>` — query-level model routing.
- All keys track `fb_samples` and `fb_ok` to separate unseen from proven-bad.

### B. Mesh Message Types (v2)
- `heartbeat`, `capability_announce`, `inference_request`, `inference_response`, `state_diff`, `registry_update`, `lora_share`, `file_request`, `file_response`.

### C. Lamport Clock Notes
- Every mesh op carries a Lamport clock from the sender.
- Dedup by `(node_id, clock, op_hash)`.
- Re-sending history on first inbound is safe because of this idempotence.

---

## 12. Conclusion

SwarmState is a **general-purpose edge intelligence layer**. The same kernel, registry, mesh, and oracle pattern works across code, emergency services, healthcare, agriculture, industry, and beyond. This spec exists so that any domain expert can adapt the system without rebuilding its core.

The cage contains context. The mesh connects bodies. The registry turns experience into judgment. Together, they form a **responsible, self-improving swarm** that helps humans work safer, faster, and calmer—without ever leaving the edge.

**Next step for any domain:** choose a 3-node demo, define the first five ops, write one oracle, and let the system learn.


---

## Appendix D. Implementation Status (agent note — NOT part of spec v1.0)

Mapped against the SwarmState repo at commit time. Kept current as features land.

| Spec element | Status | Where |
|---|---|---|
| Kernel (C), ops allowlist, deterministic validation | **SHIPPED** | C lib (`libswarmstate.so`), `harness/swarmstate.py` |
| Registry: `STRAT:*`, `MODEL:q:*` + fb_samples/fb_ok | **SHIPPED** | `harness/registry.py` (era fields, fresh-window replay) |
| Mesh: Lamport ops, dedup, default-deny allowlist | **SHIPPED** | `harness/meshd.py` + `harness/meshbridge.py` (ctypes libmesh.so) |
| Encrypted transport | **SHIPPED** (Tailcat) | `scripts/mesh_tailcat.py` + `scripts/mesh_tailcat_smoke.sh` |
| Semantic oracles `(ok, reason)` | **SHIPPED** (code domain) | `harness/task_suite.py` |
| Blind-write guard | **SHIPPED** | `harness/orchestrator.py` |
| Cross-node learning (registry bridge) | **SHIPPED** | `on_op` bridge in runners; proven 2-node |
| Contradictory-evidence refusal | **SHIPPED + demo** | `scripts/mesh_flip_demo.sh` (4/4 FLIP) |
| `inference_request` / `inference_response` | **SHIPPED + demo** | `mesh.ask()` / `mesh.serve_model()`; `backend="mesh"`; `scripts/mesh_broker_demo.sh` |
| `heartbeat` / `capability_announce` | **SHIPPED + demo** | `mesh.announce_capabilities()` / capability table + `pick_provider` scoring (model match, load, freshness); `scripts/broker_capability_demo.sh` |
| `state_diff` / `registry_update` | Covered by the op stream (Lamport-folded) | — |
| Pools + scoped bridges | **SHIPPED + demo** | `harness/pool.py` (signed expiring invites, QR via qrencode, hub/joiner) + `harness/bridge.py` (BridgePolicy: target globs, data minimization, time window; PoolBridge relays scoped learning between pools); `scripts/pool_qr_demo.sh` + `scripts/pool_bridge_demo.sh` |
| Governance (auth token + reason for high-risk ops) | **SHIPPED + demo** | `harness/governance.py` (HMAC tokens, reason, audit log, enforce/interactive); `scripts/governance_demo.sh`; EXECUTE gated out of the box |
| LoRa transport + `lora_share` | **SHIPPED + demo** | `harness/lora.py` (radio sim: frame caps, half-duplex airtime, loss + stop-and-wait ARQ); `lora_share` batch control message; `scripts/mesh_lora.py` + `mesh_lora_smoke.sh` |
| `file_request` / `file_response` | **SHIPPED + demo** | `MeshDaemon.serve_files(root, allow_patterns, max_bytes)` (default-deny: relative paths only, allowlist checked before existence so the policy never doubles as a file oracle, size cap) / `MeshDaemon.request_file(path, to, timeout)` (directed via the hello peer map, denials carry reasons); `scripts/file_exchange_demo.sh` |
