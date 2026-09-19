# Research result

**Status: Partial**

For a C99 kernel + Python harness coding-agent mesh in the StatePod style, the dominant adversarial risks are not classical BFT liveness failures but semantic damage and replay under partition-friendly CRDTs, TTL-window credential reuse, and broadcast-link crypto mistakes. The local mesh deliberately runs Lamport-ordered CRDT ops with no leader, consensus, locking, or BFT, so identical op sets converge—and hand-rolled clocks break that guarantee—while availability is protected by a fixed ring journal rather than unbounded logs.[S1][S2] Embedded llama.cpp is the documented path to a true single-binary runtime versus an external Ollama HTTP service, but it brings thread-affinity, version-deadlock, and GPU-offload traps that must be queued and policy-gated.[S7] Governance tokens and FIDO-style auth share a structural TTL-replay weakness; LoRa must stay AEAD with unique nonces and fail-closed against PSK downgrade.[S13][S18][S19][S22]

### Offline-first CRDT mesh trust (no BFT)
Peers broadcast set/delete/increment/append ops ordered by Lamport clocks; the blob is the fold of all ops, with no leader, consensus, or locking, and dedup-by-clock makes catch-up re-sends safe—so never hand-roll the counter.[S1] Unbounded journals are treated as a memory/availability bug: the C99 mesh uses a fixed power-of-2 ring (default 4096 ops) that drops the oldest entry when full, with optional compaction/checkpointing as the broader engineering mitigation.[S2]

Classical Lamport/vector clocks fail under Byzantine equivocation because timestamp uniqueness is not locally verifiable; hash-linked causal logbooks (hash chronicles), modeled as a delta-state CRDT, restore collision-resistant, Byzantine-monotonic causal history without coordination.[S3] Offline-first access-control CRDTs add concurrent-auth failure modes without classical BFT: seniority ranking is Sybil-prone, “remove both” can leave a group with no admins, and Lamport timestamps alone lack hash integrity—prefer hashes (optionally plus Lamport) and dependency acks for equivocation detection/pruning.[S4]

Identity-only post-compromise trust—revoking a replica and dropping all its updates—can break causal dependencies of later honest work; decouple identity-based from content-based trust so previously accepted updates can be whitelisted while new malicious deltas are blacklisted during deterministic reconstruction.[S5] Even validity-checked open CRDTs still allow unbounded semantic damage from well-formed malicious ops (mass deletes, huge increments); impact-proportional rate limiting (bounded Byzantine CRDTs / modified PoW) bounds aggregate adversarial effect by budget independent of Sybil count, orthogonal to quorum BFT.[S6]

### Embedded llama.cpp vs Ollama single-binary
A separate Ollama HTTP service blocks true single-binary local deployment; statically linking llama.cpp removes that dependency and HTTP overhead.[S7] The llama.cpp context must be driven only from the creating thread—off-thread calls wedge decode—so generation is serialized onto the embed_loop/model-init thread via a request queue.[S8] Older builds can stall decode via a ggml threadpool deadlock; require llama-cpp ≥ 0.3.0 with matching ggml (≥ 0.22).[S9]

On constrained VRAM, partial GPU layer offload was measured about 2× slower than CPU; auto-offload should be all-or-nothing (refuse partial fits, fall back CPU-only) and retry CPU on GPU-load failure.[S10] Embedded GBNF is used because Ollama’s generic JSON mode is schema-loose for plan output: flatten underscore-free GBNF, sampler order grammar→temp→dist, keep embedded-first with Ollama only as fallback.[S11] Even “single-service” Ollama fails operationally when systemd is inactive (manual `ollama serve`), the store/tag is wrong, or clients use `/v1`, which silently ignores `num_gpu`/`num_ctx`—use native `/api/chat`.[S12]

### Governance / FIDO2 TTL replay
Harness governance tokens are HMAC-SHA256 role-scoped credentials (`ss:<role>:<expiry-epoch>`) with default TTL 300 s; verification is constant-time and refuses expired or forged tokens.[S14] A captured live token remains valid for a second use and a different reason string within that window because tokens are TTL-window credentials for multi-op plans (accepted-by-design W1); planned mitigation is nonce/session binding.[S13] Phase-4 also accepts W3 (registry feedback without a freshness window, trusted peers only) alongside W1.[S16]

Pool-invite replay by a different node identity is denied: PoolHub tracks invite nonces single-use per identity (bounded map `MAX_USED=8192`, expired pruned) while same-id reconnect stays allowed.[S15] Mesh end-state design (roadmap, not claimed shipped kernel) specifies Ed25519 signatures over sender, timestamp, and id on envelopes to prevent replay.[S17] FIDO CTAP documents the same class of weakness for pinUvAuthToken—authenticated requests may be reordered or replayed within one token’s lifetime—mitigated in-spec by short usage-time limits (~30 s USB/BLE, ~19.8 s NFC; max usage SHOULD 10 minutes) and distinct HMAC-SHA-256 PRF argument patterns per command class.[S18]

### LoRa AES-GCM PSK mesh pitfalls (2024–2026)
Because LoRa is broadcast, the mesh requires an extra AES-256-GCM layer; the StatePod simulator already uses optional shared-PSK AES-256-GCM with a 12-byte `os.urandom` nonce prepended over CBOR (simulator path done; real radio still open).[S19] AES-GCM security collapses on IV/nonce reuse under one key: confidentiality becomes a two-time pad and authenticity can fail via GHASH-key recovery, so uniqueness is nearly as critical as key secrecy (NIST: repeat probability ≤ 2⁻³²; guard power-loss IV repetition).[S20]

Channel crypto without integrity is a major LoRa PSK-mesh pitfall: Meshtastic channels use shared-PSK AES-CTR with no MAC/AEAD, so any PSK holder can impersonate any node and known-plaintext can recover a keystream for forging under a reused IV—prefer app-level MACs / AEAD; DMs moved to X25519+AES-CCM in 2.5+ while channel AEAD remained under consideration.[S21] Silent cryptographic downgrade is a documented 2025 incident class: when PKI DMs fell back to channel PSK without UI/firmware enforcement, channel-key holders could spoof Direct Messages (CVE-2025-53627); mitigate by fail-closed rejection of legacy/unauthenticated modes (fixed in 2.7.15).[S22]

## Sources
- [S1] "agent-mesh (C99 CRDT mesh) README" — "/home/grave/Projects/internal.source/02-tools/mesh/README.md" (independently checked against "agent-mesh README + StatePod meshd/adversarial-review" — "/home/grave/Projects/internal.source/02-tools/mesh/README.md")
- [S2] "mesh.c journal_record ring overwrite" — "/home/grave/Projects/internal.source/02-tools/mesh/src/mesh.c" (independently checked against "mesh.c journal_record + engineering-rules RSS bound" — "/home/grave/Projects/internal.source/02-tools/mesh/src/mesh.c")
- [S3] "Logical Clocks and Monotonicity for Byzantine-Tolerant Replicated Data Types (PaPoC ’24)" — "https://doi.org/10.1145/3642976.3653034"
- [S4] "Notes on building a convergent, offline-first Access Control CRDT" — "https://p2panda.org/2025/08/27/notes-convergent-access-control-crdt.html"
- [S5] "Decoupling Trust in Byzantine CRDTs (arXiv:2606.31759)" — "https://arxiv.org/abs/2606.31759"
- [S6] "Bounding Byzantine Impact in Open CRDT Systems (PaPoC ’26)" — "https://dl.acm.org/doi/10.1145/3806077.3806698"
- [S7] "StatePod spec.md §7 Inference Engine" — "/home/grave/Projects/internal.source/02-tools/statepod/spec.md" (independently checked against "StatePod spec.md §7 / embedded-inference-handler-spec-v1 / GAPS.md" — "/home/grave/Projects/internal.source/02-tools/statepod/spec.md")
- [S8] "statepod-node.c embedded inference queue" — "/home/grave/Projects/internal.source/02-tools/statepod/win/statepod-node.c"
- [S9] "GAPS.md Embedded inference status" — "/home/grave/Projects/internal.source/02-tools/statepod/GAPS.md"
- [S10] "GAPS.md GPU auto-tune / NGL policy" — "/home/grave/Projects/internal.source/02-tools/statepod/GAPS.md"
- [S11] "StatePod README Embedded inference" — "/home/grave/Projects/internal.source/02-tools/statepod/README.md" (independently checked against "StatePod README Embedded inference + GAPS.md + llama_infer.c" — "/home/grave/Projects/internal.source/02-tools/statepod/README.md")
- [S12] "statepod_memory_export Ollama operational notes" — "/home/grave/Documents/statepod_memory_export.md"
- [S13] "StatePod redteam findings — WEAK W1" — "/home/grave/Projects/internal.source/02-tools/statepod/docs/redteam-findings.md"
- [S14] "StatePod harness/governance.py token API" — "/home/grave/Projects/internal.source/02-tools/statepod/harness/governance.py"
- [S15] "Federation multi-homing spec — invite hardening F.2" — "/home/grave/Projects/internal.source/02-tools/statepod/docs/federation-multihoming-spec.md"
- [S16] "Phase 4 security gate status" — "/home/grave/Projects/internal.source/02-tools/statepod/docs/phase4-security-gate.md"
- [S17] "StatePod Mesh Integration Specification §6.2" — "/home/grave/Projects/internal.source/02-tools/statepod/docs/documentation-mesh-spec.md"
- [S18] "FIDO Client to Authenticator Protocol (CTAP) v2.1 — PRF values / pinUvAuthToken" — "https://fidoalliance.org/specs/fido-v2.1-ps-20210615/fido-client-to-authenticator-protocol-v2.1-ps-errata-20220621.html#prfValues"
- [S19] "StatePod LoRa AES-GCM implementation + mesh-spec v2 §7.2" — "/home/grave/Projects/internal.source/02-tools/statepod/harness/lora.py"
- [S20] "NIST SP 800-38D Recommendation for GCM" — "https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38d.pdf"
- [S21] "Meshtastic Encryption Known Limitations" — "https://meshtastic.org/docs/about/overview/encryption/limitations/"
- [S22] "CVE-2025-53627 Meshtastic PKI-to-PSK downgrade" — "https://www.cve.org/CVERecord?id=CVE-2025-53627"

## Coverage and uncertainty
- "Question 1 uncertainty: No inspected 2024–2026 primary source jointly analyzes the specific C99 kernel + Python harness coding-agent mesh against the non-BFT CRDT failure-mode literature; local mesh evidence and research evidence are separate."
- "Question 1 uncertainty: The local mesh wire format trusts string clock.origin with no signatures in mesh.h/mesh.c; Byzantine forgery of origin/counter is an implied gap, not an explicitly authored threat-model section."
- "Question 1 uncertainty: Whether PaPoC ’26’s impact-bounding PoW is practical for a tiny-RSS C99 coding-agent mesh is not addressed by that paper or by the local mesh docs."
- "Question 1 uncertainty: Aries Mesh LWW+Lamport partition reordering notes were only seen via secondary search summaries, not by inspecting the GitHub tree locally, so they were omitted from claims."
- "Question 2 uncertainty: Official Ollama packaging (tarball + /usr/lib/ollama + systemd) and llama-server subprocess architecture were only seen via web-search summaries of docs.ollama.com/linux and github.com/ollama/ollama llm sources—not fetched/inspected as raw pages in this session—so they were omitted as standalone claims."
- "Question 2 uncertainty: Generic OOM preflight wording (“model requires more system memory…”) and third-party llama.cpp-vs-Ollama embedding blogs were not used as evidence because primary pages were not inspected."
- "Question 2 uncertainty: remote-pi-mesh get_messages could not be invoked: CallMcpTool is not in this subagent’s available tool list."
- "Question 3 uncertainty: No StatePod/agent-mesh repository document inspected here prescribes adopting FIDO2/WebAuthn as mesh join or governance-token auth for the C99 kernel + Python harness."
- "Question 3 uncertainty: W3C WebAuthn challenge/signCount replay protections were summarized by search of w3.org but the full TR HTML was not opened locally, so those RP-side rules are omitted from claims."
- "Question 3 uncertainty: documentation-mesh-spec Ed25519 anti-replay is explicit roadmap text; whether a C kernel implementation should prefer that over today’s HMAC invite/governance framing is not decided in the inspected shipped code."
- "Question 4 uncertainty: Primary web pages (NIST PDF, Meshtastic HTML, NVD/CVE records) were inspected only via web-search excerpts, not a full raw fetch of each document body."
- "Question 4 uncertainty: StatePod LoRa AES-GCM uses random 12-byte nonces; whether field-scale traffic will approach NIST’s RBG-IV birthday/invocation limits, or whether hardware will need persistent senderID||counter nonces, is not measured in-repo."
- "Question 4 uncertainty: No inspected 2024–2026 primary source demonstrates a public AES-GCM nonce-reuse exploit specifically on a deployed LoRa mesh product; MeshVani/blog material on reboot-reset counters was secondary and omitted from claims."
- "Question 4 uncertainty: How Meshtastic PHY/SDR replay and NodeDB/TOFU eviction attacks map onto StatePod’s allowlist + CRDT op model was not directly evidenced in inspected sources."
- "Question 4 uncertainty: Real StatePod LoRa radio hardware remains unimplemented (simulator only), so hardware entropy, SPI/DMA corruption, and secure PSK storage pitfalls are not yet empirically validated for this codebase."
- "Claim claim-21 was excluded by verification: CVE-2025-52464 evidences cloned/low-entropy per-node X25519 DM keys, not Shared-PSK channel-key failures; the exact Shared-PSK∪cloned-identity framing and common-LoRa-PSK transfer claim are not directly supported by the cited NVD evidence.."
- "Claim claim-23 was excluded by verification: Cited redteam-findings.md supports W1 TTL token reuse and W2 single-use-per-identity invites, but does not evidence the 'LoRa literature warns about' application-layer replay class or the AEAD-on-radio conclusion in the exact statement.."
