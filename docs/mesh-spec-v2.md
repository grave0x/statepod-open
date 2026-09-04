<!--
  SwarmState Mesh Specification v2.0
  Author: @grave0x  |  Date: August 2026
  Status: Design / Roadmap (product vision)
  Transport: Tailcat (primary) + LoRa mesh (optional field) + local discovery
  Note: The IMPLEMENTATION is layered on the shared C mesh base
        (~/Projects/02-tools/mesh, JSON op wire format) per the
        engineering rules -- see docs/mesh-phase1-implementation.md.
-->

## SwarmState Mesh Specification v2.0
**Status:** Design / Roadmap  
**Dependency:** [tailcat](https://github.com/tailscale/tailcat) (primary transport) + LoRa mesh (optional field transport)  
**Author:** @grave0x  
**Date:** August 2026  

---

## 1. Executive Summary

SwarmState Mesh extends the single-device kernel into a **private, self-organizing network of devices** that share inference, state, and learned outcomes. The mesh is transport-agnostic: it runs over Tailcat for standard networks, LoRa mesh for infrastructure-free environments, and local discovery (mDNS/QR/BLE) for ad-hoc pairing.

The key insight: **SwarmState's 62-byte prompts and semantic output make mesh inference practical on almost any transport.** A state diff is a few hundred bytes. A plan request is under a kilobyte. Even a low-bandwidth LoRa link can carry a full inference request, making distributed AI viable in places no cloud could reach.

**Primary use cases are civilian first**—agriculture, search and rescue, field work, education, emergency services—with military/defense as one deployment context among many. The same technology that helps a farmer monitor 5,000 acres can help a rescue team coordinate in a flood zone, or a school run a private AI lab without cloud costs.

---

## 2. Core Philosophy

> *The kernel contains the context. The mesh connects the bodies. The registry remembers what each body learns.*

| Principle | Implication |
|-----------|-------------|
| **Civilian-first, defense-capable** | The default framing is agriculture, rescue, education, and small business. Military use is real but not the design center. |
| **Transport-agnostic** | Tailcat for IP networks, LoRa for long-range/low-bandwidth, local discovery for zero-config pairing. Same message schema everywhere. |
| **No cloud, no control plane** | Nodes connect directly via tokens or pre-shared keys. No external coordination server. |
| **Tiny messages by design** | The mesh never moves raw video or full files—only semantic summaries, state diffs, and registry updates. |
| **Trust is explicit** | Nodes join by scanning a QR code, sharing a token, or exchanging keys out-of-band. No open discovery unless you opt in. |
| **The registry is collective memory** | Success rates, timings, and feedback aggregate across nodes, so the whole mesh learns faster than any single device. |

---

## 3. Problem Statement

A single SwarmState instance is powerful but isolated. Real-world work happens across multiple devices, in places without reliable internet, or in teams where compute should be shared.

**Without the mesh:**
- A farmer must drive back to the homestead to analyze drone footage.
- A search-and-rescue team can't share inference when cell towers are down.
- A school computer lab burns cloud API budget because each thin client is too weak for local inference.
- A field researcher carries a powerful laptop but still can't use it to help a colleague's phone-based model.

**With the mesh:**
- Any device can ask "who has capacity?" and receive help.
- The registry learns from everyone's tasks, not just one device's.
- State stays in sync across the team without cloud sync.
- The system keeps working when the internet doesn't.

---

## 4. Transport Layer

### 4.1 Primary: Tailcat

[tailcat](https://github.com/tailscale/tailcat) provides point-to-point WireGuard-encrypted tunnels without a control plane. It's userspace-only (no root), handles NAT traversal via DERP, and supports token-based addressing.

**SwarmState integration:**
- Each node runs a Tailcat server (listener) that accepts connections on a well-known port.
- The node prints a connection token (`tc...`). This token is the entire invitation.
- Other nodes connect by passing the token to the Tailcat client.
- All SwarmState messages (JSON) flow over this encrypted tunnel.

**Node identity:**
- Tailcat keys are saved to disk so addresses remain stable across restarts.
- The registry stores known node tokens and maps them to friendly names ("Farm Truck", "Lab PC 3", "Field Phone").
- `--allow` restricts which client keys may connect.

**DNS/Discovery:**
- A node's token can be published as a DNS TXT record (`tailcat=tc...`).
- Nodes can also advertise via mDNS on local networks.

### 4.2 Secondary: LoRa Mesh

For environments without Wi-Fi, cell coverage, or any fixed infrastructure, LoRa mesh provides long-range, low-power communication.

**Characteristics:**
- Range: 1–10+ km per hop (line-of-sight), extensible via mesh relays.
- Bandwidth: 0.3–5 kbps (very low).
- Power: milliwatts; nodes can run for days on small batteries.
- Topology: peer-to-peer mesh with flooding or directed diffusion.

**Why it works with SwarmState:**
- A plan request is ~62 bytes of prompt + schema overhead.
- A plan response is ~200–500 bytes of JSON.
- A state diff for a single file edit is ~100–500 bytes.
- A registry update for 10 entries is <1 KB.

SwarmState's messages are *already* small enough for LoRa. The mesh just needs a thin framing layer.

**Integration approach:**
- A LoRa adapter in the mesh manager handles packetization, acknowledgements, and retries.
- Messages are serialized as CBOR (not JSON) to minimize bytes.
- Critical messages (inference requests, state diffs) get priority over registry updates and heartbeats.
- The registry learns which LoRa routes are reliable and caches messages until confirmed delivery.

### 4.3 Local Discovery & Pairing

For devices on the same Wi-Fi or physically close:

- **QR code pairing**: A node displays a QR containing its Tailcat token. A phone scans it, the mesh manager parses, and the node is added to the allowlist. Perfect for field teams and classrooms.
- **mDNS advertisement**: Nodes advertise `_swarmstate._tcp.local` on the local network. The UI shows a list of discoverable nodes. One tap to join.
- **Bluetooth LE / NFC**: For offline pairing when Wi-Fi isn't available but devices are within a few meters. Exchange Tailcat tokens over BLE, then establish the tunnel via DERP or direct UDP.

The goal: **a primary school kid should be able to join two devices to the same mesh.**

---

## 5. Message Protocol

All messages are JSON objects wrapped in an encrypted envelope (Tailcat already provides the encryption; the envelope adds versioning and routing metadata).

```json
{
  "version": 2,
  "type": "inference_request",
  "id": "a1b2c3...",
  "timestamp": 1724800000,
  "sender": "farm-truck",
  "payload": { ... }
}
```

### 5.1 Message Types

| Type | Direction | Payload | Typical Size |
|------|-----------|---------|--------------|
| `heartbeat` | Any → Any | Node status, resource snapshot | ~100 B |
| `capability_announce` | Any → Any | Available models, LoRA adapters, compute capacity | ~200 B |
| `inference_request` | Any → Any | Task description, context strategy, preferred model | ~300 B |
| `inference_response` | Any → Any | Plan JSON or error, token usage, duration | ~500 B |
| `state_diff` | Any → Any | Repo-relative path, diff content, parent state hash | ~500 B |
| `registry_update` | Any → Any | Aggregated PerfEntry records (signature, strategy, timings, feedback) | ~2 KB |
| `lora_share` | Any → Any | LoRA adapter ID, base model, weights hash, metadata | ~10 MB (if weights transferred separately) |
| `file_request` | Any → Any | Request for specific file content (rare—state diffs usually enough) | ~100 B |
| `file_response` | Any → Any | File content (only if explicitly requested) | Varies |

---

## 6. Core Services

### 6.1 Shared Inference

The mesh's primary function: route inference requests to the best available node.

**Inference Broker:**
- Each node advertises capabilities via `capability_announce`: models loaded, LoRA adapters, GPU/CPU availability, current load, battery level.
- The orchestrator's `ask()` gains a `mesh://` backend option.
- When a task needs a plan, the broker:
  1. Scores candidate nodes using the registry (historical success for this signature + model) and current resource snapshot.
  2. Routes the request to the highest-scoring node.
  3. Returns the first valid response.
  4. Falls back to local inference or another node on timeout.

**Node roles:**
- **Provider**: Has a model loaded and can serve inference requests.
- **Consumer**: Requests inference from other nodes.
- **Relay**: Forwards messages in a multi-hop mesh (especially LoRa).
- **All**: Most nodes will be both provider and consumer.

**Example scenario (farm):**
- A farmer's phone (consumer) captures a photo of a diseased crop leaf.
- The phone requests inference from the farm truck's laptop (provider, running a 7B model).
- The laptop processes, returns a 200-byte summary: "Likely fungal infection; see section 3 of field guide."
- The phone displays the result. The farmer drives out to treat the affected area.

### 6.2 State Sync

Keeps repo state consistent across nodes.

- Uses the kernel's event log (JSONL). Each entry has `path`, `content_hash`, `prev_hash`, and timestamp.
- On a `WRITE`, the node broadcasts a `state_diff` to peers.
- Receiving nodes apply the diff and update their local state.
- Conflicts resolved by timestamp + parent hash; kernel checkpoints allow rollback.

**Why it matters:**
- A rescue team updates a map of searched areas on one device; the update propagates to all team members via mesh.
- A farmer records equipment maintenance notes on the truck laptop; the phone gets the update when it reconnects.

### 6.3 Registry Aggregation

The mesh's collective memory.

- Nodes periodically broadcast `registry_update` messages containing new PerfEntry records.
- Receiving nodes merge into their local registry.
- Entries keyed by `signature|strategy|model` (and soon `:model` dimension).
- Over time, all nodes benefit from each other's experience.

**Effect:**
- A 1.5B model that fails on a task for one node's environment (e.g., poor lighting in drone images) gets flagged in the registry.
- Other nodes route around it automatically.
- The whole mesh gets smarter without any central coordination.

### 6.4 LoRA Exchange

LoRA adapters (10 MB) can be shared between nodes that trust each other.

- `lora_share` announces availability; `file_request`/`file_response` transfer the weights.
- Hash verification ensures integrity.
- Only nodes on the allowlist can exchange LoRAs.

**Use case:**
- A school fine-tunes a LoRA adapter on the introductory Python curriculum.
- Teachers share it across the lab mesh.
- Each thin client uses it automatically when the task matches the signature.

---

## 7. Security Model

### 7.1 Trust Boundaries

- **Default deny**: Nodes do not accept connections unless explicitly allowed.
- **Pairing**: QR code, token exchange, or pre-shared key. No open discovery unless the user enables it.
- **Allowlists**: Tailcat's `--allow` restricts which client keys may connect.
- **Registry scoping**: Each node can choose which registry entries to share (e.g., share strategy success but not task content).

### 7.2 Encryption & Authentication

- Tailcat provides end-to-end WireGuard encryption and key-based authentication.
- LoRa mesh requires an additional encryption layer (since LoRa is broadcast). AES-256-GCM with pre-shared or exchanged keys.
- All message integrity is verified before processing.

### 7.3 Privacy

- **No cloud**: All inference and state stays within the mesh.
- **No raw data sharing**: Only plans, state diffs, and semantic summaries move across the mesh. Raw files and images stay local unless explicitly requested.
- **Anonymized registry**: Registry updates can be configured to share only aggregated statistics, not individual task details.

---

## 8. Use Cases

### 8.1 Agriculture (Civilian)

**Scenario:** A farmer manages 5,000 acres of mixed cropping and grazing with no reliable internet beyond the homestead.

**Setup:**
- Farm truck: laptop running SwarmState with 7B model, acting as primary inference provider.
- Farmer's phone: 1.5B model, joins mesh when within range or via LoRa.
- Solar-powered LoRa nodes at key points (gates, water tanks, cattle yards).
- Drone: lightweight camera + 1.5B vision model for aerial surveys.

**Daily use:**
- Drone flies a pasture, captures images, runs on-device inference for weed detection.
- Results (tiny semantic summaries) sent via LoRa to the truck laptop.
- Laptop updates the farm map with affected areas.
- Farmer's phone syncs the map and provides directions to the next spot.
- Soil sensors report moisture data; a small model interprets and flags irrigation issues.
- Registry learns which conditions correlate with disease outbreaks.

**Result:** The farmer gets real-time, local intelligence without cell coverage or cloud subscriptions. The mesh pays for itself in a season.

### 8.2 Search and Rescue (Emergency Services)

**Scenario:** A flood has wiped out cell towers. A rescue team of 8 people with phones and two rugged laptops coordinates across a 20 km² area.

**Setup:**
- Two field laptops run 7B models, serve as inference providers.
- Each team member's phone runs a lightweight client or 1.5B model.
- LoRa mesh (or Wi-Fi where available) connects the team.
- Drones with camera + 1.5B vision model survey inaccessible areas.

**Use:**
- Drone captures debris field imagery; on-device inference identifies possible survivors (heat signatures, movement).
- A 100-byte report ("3 possible survivors, coordinates X/Y, confidence 0.87") is sent over LoRa.
- The team leader's laptop aggregates reports and updates the search map.
- Registry learns to distinguish debris from people in flood conditions.
- Team members share state diffs (marked-searched areas) so no one double-searches.

**Result:** Faster, more coordinated response without relying on external infrastructure. The system gets better with each mission.

### 8.3 Education (Schools & Universities)

**Scenario:** A school computer lab with 30 thin clients (8 GB RAM, no GPUs) wants to teach coding with AI assistance.

**Setup:**
- Two teacher workstations run 7B models, act as inference providers.
- 30 thin clients run SwarmState kernels with 1.5B models (or no local model—just the kernel + consumer role).
- The lab mesh connects everything via Wi-Fi/Ethernet.
- A shared LoRA adapter tuned to the curriculum is distributed across the mesh.

**Use:**
- Students ask for help with Python loops; the request routes to a teacher workstation.
- The local 1.5B handles simple tasks (syntax checks, formatting); complex tasks escalate to 7B.
- The registry learns which task types students struggle with, enabling the system to proactively suggest examples.
- All code stays private on the school network.
- No cloud API costs.

**Result:** A private, self-improving coding assistant for every student at zero marginal cost.

### 8.4 Remote Field Work (Mining, Surveying, Environmental Monitoring)

**Scenario:** Geologists at a remote survey site need to analyze rock samples and share findings across the team.

**Setup:**
- Survey vehicle carries a laptop with 7B model (inference provider).
- Field scientists carry phones with 1.5B models.
- LoRa mesh connects the team when they're out of range of the vehicle's Wi-Fi.
- A database of known mineral signatures is stored in the kernel's docs layer.

**Use:**
- Scientist photographs a rock sample.
- Phone runs on-device inference (1.5B) for initial classification.
- If confidence is low, the request escalates to the vehicle's 7B model over LoRa.
- The result is returned as a text summary with references to the local database.
- All data stays within the team's mesh—no sensitive geological data goes to the cloud.

### 8.5 Emergency Services (Fire)

**Scenario:** A bushfire response team needs real-time situational awareness across a 50 km² fireground.

**Setup:**
- Incident commander has a rugged laptop with 7B model.
- Crew leaders carry phones with 1.5B models.
- Drones with thermal cameras run on-device inference for hotspot detection.
- LoRa mesh connects the team where radio is unreliable.

**Use:**
- Drone detects a new hotspot; sends coordinates + confidence to the incident commander.
- Commander's laptop aggregates and predicts fire spread using local weather data.
- Crew leaders receive targeted updates on their phones: "Move to sector 7, fire approaching from south."
- Registry learns which patterns indicate dangerous conditions.

### 8.6 Military/Defense (Acknowledged, Not Primary)

The same technology serves defense edge computing: squad-level mesh sharing inference for object detection, terrain analysis, and language translation on captured documents. Low cost, no cloud, mechanical constraints. But this is *one* deployment context, not the design center.

### 8.7 Home/Consumer

Multiple personal devices sharing inference:
- Desktop (7B) serves as the home inference hub.
- Laptop and phone (1.5B) join the mesh automatically.
- State syncs across devices (e.g., notes, task lists).
- Registry learns which tasks to run locally vs. escalate to the desktop.
- Private by design—no cloud, no ads, no data collection.

---

## 9. Node Roles & Capabilities

| Role | Description | Example Hardware |
|------|-------------|------------------|
| **Inference Hub** | Runs larger models (7B+), serves requests from other nodes | Workstation, gaming PC, server |
| **Edge Node** | Runs small models (1.5B), handles local tasks, escalates when needed | Laptop, phone, tablet |
| **Thin Client** | No local model; sends all inference to the mesh | Old PC, low-power device |
| **Relay** | Forwards messages in multi-hop mesh (especially LoRa) | Solar-powered LoRa node |
| **Sensor Node** | Captures data (camera, soil, temperature), runs tiny on-device model | Drone, IoT device |

A node can be one or more of these roles simultaneously.

---

## 10. Performance Expectations

| Metric | Target |
|--------|--------|
| Inference request round-trip (same LAN) | < 500 ms (including model time) |
| Inference request via LoRa (single hop) | < 10 s (including model time) |
| State diff propagation (same LAN) | < 100 ms |
| Registry update size (100 entries) | < 5 KB |
| Battery impact (phone, idle mesh) | < 3% per hour |
| Node join time (QR code pairing) | < 5 seconds |

---

## 11. Roadmap

### Phase 1: Two-Node Mesh Core
- Integrate Tailcat as transport.
- Implement capability advertisement and inference broker.
- Demo: phone requests inference from laptop.

### Phase 2: State Sync & Registry
- Implement state diff propagation.
- Registry aggregation across nodes.
- Test with 3-node mesh (laptop, desktop, phone).

### Phase 3: Ad-Hoc & Field
- QR/mDNS pairing.
- LoRa mesh adapter.
- Test in a real outdoor environment (no Wi-Fi).

### Phase 4: LoRA Exchange
- Adapter sharing with hash verification.
- Automatic selection based on registry success.

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Network latency makes remote inference slower than local | Registry learns per-node latency; fallback to local |
| State sync conflicts | Parent hash + timestamp; kernel checkpoints allow rollback |
| LoRa packet loss | Acknowledgements + retries; critical messages prioritized |
| Security breach (compromised node) | End-to-end encryption, allowlists, node revocation |
| Battery drain (mobile) | Mesh only active when needed; aggressive sleep mode |
| Public DERP rate limits | Run own DERP relay (Tailcat supports `--region=derp.example.com`) |

---

## 13. Open Questions

1. **Discovery scope**: Should mDNS advertising be opt-in or opt-out? (Security vs. convenience tradeoff.)
2. **Mesh size limits**: How many nodes before registry aggregation becomes noisy? (Likely 10–50 for personal/team use; larger meshes need hierarchical scoping.)
3. **Privacy policy**: What exactly gets shared in registry updates? (Proposal: aggregate stats by default, raw entries only if user opts in.)
4. **LoRa licensing**: In some countries, LoRa frequency bands require licensing. Ensure compliance.

---

## 14. Conclusion

SwarmState Mesh turns a single-device, context-containing kernel into a **private, self-organizing network of devices that share inference, state, and learned outcomes**. The transport is flexible—Tailcat for standard networks, LoRa for infrastructure-free, local discovery for zero-config pairing—but the message schema and security model are consistent.

The primary use cases are civilian: agriculture, search and rescue, education, field work, emergency services. The same technology has defense applications, but that's not the design center. The point is to make **distributed, self-improving AI practical on any hardware, anywhere, without cloud dependency**.

The swarm is not a weapon. It's a tool. And the cage stays closed.