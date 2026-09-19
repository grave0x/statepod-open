# StatePod Modular UI Kit Specification

**Version:** 1.0
**Status:** Draft for discussion
**Purpose:** Define a flexible, component-based interface layer that can be assembled for any client, domain, or device without rebuilding the core.

## 1. Design Principles

| Principle | Meaning |
|-----------|---------|
| **Modular first** | Every widget is independent, with its own data source, permissions, and API. |
| **Assembled, not built** | UIs are composed from existing widgets, not custom-coded per client. |
| **Offline-first** | Served from the local node. No internet dependency. |
| **Role-aware** | Widgets and actions adapt to who is looking (supervisor, field worker, admin). |
| **Touch-friendly** | Large targets, high contrast, works with gloves and in bad light. |
| **Voice-optional** | Every major action can be triggered by voice. |
| **Calm by default** | Less is more. Only show what's needed now. Alerts are rare and meaningful. |
| **Accessible** | Plain language, large text, clear icons. No jargon. |

## 2. Core Architecture

```
┌─────────────────────────────────────────────┐
│               UI Shell                       │
│  (served from node, runs in any browser)    │
├─────────────────────────────────────────────┤
│  Widgets: Map, Tasks, Alerts, Roster, etc.  │
├─────────────────────────────────────────────┤
│  Widget API / State Bindings                │
├─────────────────────────────────────────────┤
│  Mesh State + Registry + Governance         │
└─────────────────────────────────────────────┘
```

- **UI Shell** – the page or app frame that hosts widgets.
- **Widgets** – self-contained modules with their own state, actions, and permissions.
- **State Bindings** – each widget subscribes to specific keys in the mesh state. When state changes, the widget updates.
- **Core** – the kernel, registry, and mesh. The UI never talks to these directly; it talks to a small local API.

## 3. Widget Catalogue

### 3.1 Map — live positions, markers, polygons, routes (GPS, node locations, hazard zones).
### 3.2 Task List — current/assigned/completed tasks (mesh ops, task signatures, registry).
### 3.3 Alert Banner — high-priority alerts, clear language (oracles, governance denials, sensor thresholds).
### 3.4 Team Roster — people, skills, tools, location, status (capability records, node identity).
### 3.5 Vitals / Sensor Panel — live telemetry with thresholds and trends.
### 3.6 Governance Console — high-risk actions requiring human approval (governance ledger, audit log).
### 3.7 Voice Input — push-to-talk or tap-to-speak commands and reports.
### 3.8 Knowledge Search — queries the docs layer.
### 3.9 QR / Invite — show or scan a QR code to join a pool or add a node.
### 3.10 Status Dashboard — overview of all nodes, services, and mesh health.

## 4. Role-Based Views

| Role | Typical Widgets | Notes |
|------|-----------------|-------|
| **Field Worker** | Map, Task List, Alert Banner, Voice Input | Minimal clutter. Big buttons. |
| **Supervisor** | Roster, Task List, Governance Console, Alerts | Approval power. Overview plus drill-down. |
| **Admin** | QR/Invite, Status Dashboard, Knowledge Search | Configuration, onboarding, audit access. |
| **Observer** | Map, Alerts, Knowledge Search | Read-only. |

Role names and permissions are re-combinable per client (farm: Owner/Worker/Agronomist; hospital:
Nurse/Doctor/Ward Clerk; CFS: Captain/Crew Leader/Volunteer).

## 5. Wireframe-First Co-Design

Widget cards + blank screen frames → co-design session → config file.

```yaml
role: Volunteer
widgets:
  - type: map
    size: full
  - type: alert_banner
    position: top
  - type: voice_input
    position: bottom_right
```

## 6. Implementation Notes

- Minimal framework (Vue/Alpine or none); UI served from the local node over HTTP; WebSocket/SSE for live updates.
- Each widget declares the state keys it cares about; the shell subscribes and pushes updates.
- Widgets may be visible but not interactive per role; governance still enforces high-risk approval.
- Offline: UI loads from the node; last-known state persists; queued ops publish on reconnect.

## 7. First Build Milestone

**Deliverables:** (1) UI Shell served from the node; (2) six core widgets — Map, Task List, Alert
Banner, Roster, Governance Console, QR/Invite; (3) role views Field Worker + Supervisor; (4) config
file for widget layout; (5) wireframe card set (PDF or printable).

**Success criteria:** a non-technical user opens a browser and understands the dashboard; a
supervisor approves/denies a high-risk action from the UI; the UI works offline on a phone on the
node's Wi-Fi; the layout changes by editing a config file, not code.

## 8. Future Extensions

Native apps (action button, background mesh, push), voice-first mode, night/smoke themes,
multi-language, wearables, third-party widget developer API.

---

*Living and modular — like the UI kit it describes.*

---

## Appendix A. Build Status — C node web UI (agent note — NOT part of the draft)

The first-build milestone shipped in the Windows/standalone node
(`win/statepod-node.c`, web UI served by the node itself):

| Spec item | Status | Details |
|---|---|---|
| UI shell served from the node | **SHIPPED** | single-page HTML over `/`, 2 s `fetch('/api/state')` polling |
| Config-driven layout | **SHIPPED** | `config/widgets.json` served at `/api/config` (widget list + roles); layout edits are config, not code |
| Widgets: status, tasks, alerts, roster, gov, QR | **SHIPPED** | `status` (kernel/hash/ops/disk/peers), `tasks` (local registry partition), `alerts`, `roster` (capability announcements), `gov` (copy-token mint/approve/deny), `qr` |
| QR / invite widget (3.9) | **SHIPPED** | renders a REAL QR: the pool invite (`st.join`) when joined, else the join line; SVG via an embedded MIT qrcode-generator served at `/qr.js` (byte-array embedded at build time, `QR_LIB_LEN` macro); copy button |
| Federation context switcher | **SHIPPED** | top-bar `select` (all pools / per-pool) + `fed` widget rendering each foreign pool's STRAT/MODEL partition; local `tasks` widget shows ONLY the local partition |
| Role views | **SHIPPED** | `field_worker`, `supervisor`, `admin`, `observer`; widget lists per role (from config, with ROLES defaults); `?role=`/`#hash` selection |
| Offline-first | **SHIPPED** | UI is fully static once loaded; no external CDNs |
| Governance approve/deny from the UI | **SHIPPED (copy-token)** | Mint/verify/decide over `GET /api/gov/*`; tokens interoperable with `harness/governance.py`. POST JSON route waits on Strix gate. |

**Quality notes:** the served SPA passes `node --check` (a pre-existing
nested-backtick bug in the QR widget made the shipped JS unparseable and
was fixed); `win/statepod-win-v1.zip` ships the verified build; the
SPA exposes no secrets (the `join` invite is intentionally public — it IS
the join credential).
