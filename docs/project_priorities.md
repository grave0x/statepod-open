# SwarmState Project Priorities & Self-Referential Improvement Loop

Generated: 2026-09-04
Source: 104 exported DeepSeek chat sessions (~12.2 MB)

## Executive Summary

| Priority | Count | Total Days |
|----------|-------|------------|
| Critical | 3 | 7 |
| High | 4 | 15 |
| Medium | 4 | 14 |
| Low | 4 | 19 |
| **Total** | **15** | **52** |

The project portfolio is organized into 4 phases matching SwarmState's existing roadmap.

---

## Phase 1: Kernel Spike (Foundation)

### 🔴 Critical Path Projects

These projects form the foundation for all subsequent development and can proceed in parallel.

#### 1. Embedded Inference Engine (2 days)
**Dependencies:** None  
**Outputs:** `libllama.a`, `kernel/c/src/lora.c` modifications, `bindings/swarmstate.py` updates  
**Goal:** Working LLM inference in C daemon with static llama.cpp linkage, prefix caching, GBNF grammar enforcement, and auto-tuning GPU layers  
**Status:** Not Started

#### 2. Governance UI Enhancement (2 days)
**Dependencies:** None  
**Outputs:** UI components in `harness/main.py`, kernel approval flags in `state.c`, updated `docs/ui-kit-spec-v1.md`  
**Goal:** Interactive governance console with recommendation card, approve/deny buttons, context panel, audit log, and emergency override  
**Status:** Not Started

#### 3. LoRa Hardware Validation & Procurement (3 days)
**Dependencies:** None  
**Outputs:** Hardware procured (RAK4631 kit x3), field test results, antenna configurations  
**Goal:** Three-node mesh ready for deployment  
**Status:** Not Started

#### 4. Kernel Core Operations (3 days)
**Dependencies:** None  
**Outputs:** `kernel/c/src/ops.c`, `kernel/c/src/state.h` API header, `bindings/swarmstate.py`  
**Goal:** Basic file READ/WRITE/GREP/AST operations functional in C kernel  
**Status:** Not Started

---

## Phase 2: State & Persistence (Core Systems)

### 🟠 High Priority Projects

#### 5. Mesh Routing & Network Resilience (5 days)
**Dependencies:** Kernel Core Operations  
**Outputs:** `harness/bridge.py`, `kernel/c/src/registry.c`, `docs/mesh-spec-v2.md`  
**Goal:** Self-healing mesh network with gossip protocol, DHT, and CRDT synchronization  
**Status:** Not Started

#### 6. State Persistence & Checkpoint/Rollback (4 days)
**Dependencies:** Kernel Core Operations  
**Outputs:** JSONL event log, checkpoint/rollback functions, `scripts/build_kernel.sh`  
**Goal:** Crash-resistant state persistence  
**Status:** Not Started

#### 7. C Kernel Python ctypes Binding (3 days)
**Dependencies:** Kernel Core Operations  
**Outputs:** `harness/state.py`, `bindings/swarmstate.py`, `harness/orchestrator.py`  
**Goal:** Python FFI wrapper for all kernel operations  
**Status:** Not Started

---

## Phase 3: Context Strategy & UI (User-Facing)

### 🟡 Medium Priority Projects

#### 8. Maturity Bench Script & Test Suite (2 days)
**Dependencies:** State Persistence, ctypes Binding  
**Outputs:** `scripts/maturity_bench.py`, `harness/test_maturity_bench.py`  
**Goal:** Automated performance regression testing  
**Status:** Not Started

#### 9. UI Shell with Six Core Widgets (4 days)
**Dependencies:** Governance UI, ctypes Binding  
**Outputs:** `mobile/flutter/` app, `harness/ui.py`, updated `docs/ui-kit-spec-v1.md`  
**Goal:** Functional mobile/web UI with Map, Task List, Alert Banner, Roster, Govern, and Context widgets  
**Status:** Not Started

#### 10. Third-Party Domain Widget System (3 days)
**Dependencies:** UI Shell  
**Outputs:** `docs/federation-multihoming-spec.md`, `harness/widget_loader.py`  
**Goal:** Plugin architecture for community-developed widgets  
**Status:** Not Started

#### 11. Energy Harvesting & Autonomous Power (3 days)
**Dependencies:** LoRa Hardware Validation  
**Outputs:** `hardware/solar_controller.py`, `docs/field-node-quickstart.md`  
**Goal:** Mesh nodes can operate off-grid for 7+ days  
**Status:** Not Started

---

## Phase 4: Security Gate & Advanced Features

### 🟢 Low Priority / Future Projects

#### 12. MSP Pilot Program Preparation (5 days)
**Dependencies:** Embedded Inference, Governance UI, LoRa Hardware, Maturity Bench  
**Outputs:** `docs/support-ops-msp-spec-v1.md`, pilot brief document  
**Goal:** MSP-ready SwarmState deployment for Port Augusta pilot  
**Status:** Deferred (waiting for successful field test)

#### 13. Stigmergic Kernel Prefetch (4 days)
**Dependencies:** State Persistence, Mesh Routing  
**Outputs:** Prefetch logic in `state.c`, updated `docs/technical-spec.tex`  
**Goal:** Predictive context loading reduces latency by 40%  
**Status:** Not Started

#### 14. Allosteric LoRA Blending (5 days)
**Dependencies:** Embedded Inference, Stigmergic Prefetch  
**Outputs:** Enhanced `kernel/c/src/lora.c`, `harness/lora_blender.py`  
**Goal:** Adaptive model specialization for different tasks via token-level adapter fusion  
**Status:** Not Started

#### 15. Environmental Sensing & Bushfire Prediction (4 days)
**Dependencies:** LoRa Hardware, Energy Harvesting  
**Outputs:** `hardware/sensor_integration.py`, `docs/native-app-scope-v1.md`  
**Goal:** Deployable environmental monitoring mesh  
**Status:** Not Started

---

## Self-Referential Improvement Loop (SRIL)

A continuous feedback system that improves its own project planning and execution.

### Components

| Component | Purpose | Inputs | Outputs |
|-----------|---------|--------|---------|
| Feedback Collection | Capture execution metrics | Actual vs estimated time, dependency resolution, output quality | `improvement_metrics.jsonl` |
| Priority Adjustment | Dynamic priority re-ranking | Metrics, business impact, risk assessments | `updated_priority_matrix.json` |
| Dependency Optimizer | Identify optimization opportunities | Completion times, blocking deps, parallelization | `optimized_dependency_graph.json` |
| Planning Accuracy Trainer | Improve estimation models | Historical data, task complexity | `estimation_model.pkl`, `accuracy_report.json` |

### Weekly Execution Cycle

1. **Collect feedback** from completed projects (actual time, quality, blockers)
2. **Update estimation models** based on deviation analysis
3. **Re-prioritize** backlog using fresh impact/risk data
4. **Optimize dependency graph** via critical path analysis
5. **Generate updated plan** for next sprint
6. **Execute** next sprint using the improved plan

### Trust Boundary

The improvement loop operates **within the harness layer only** — it adjusts plan quality, estimation accuracy, and prioritization, but **cannot modify kernel-level safety guarantees** or override the approval-required governance model. This ensures self-improvement stays bounded and safe.
