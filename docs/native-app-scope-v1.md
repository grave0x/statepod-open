# StatePod Standalone Native App Scope

**Version:** 1.0
**Status:** Draft for discussion
**Purpose:** Define the first native app experience for users who are not yet part of a mesh,
and show how they can grow into federation later.

## 1. Core Philosophy
The first app is NOT a mesh dashboard — it is a private, local AI assistant that works alone on
one device. The mesh is an optional feature, not a requirement. A user should use the app
meaningfully for days/weeks and only later discover it can connect to other devices.

## 2. What the First App Includes
- **Standalone core:** private AI assistant (1.5B local default, optional explicit cloud
  fallback); knowledge folder (index docs/notes/PDFs locally, answer from them); task execution
  (read/write files, approved commands, summarise, answer); governance (high-risk actions require
  approval, everything logged); registry (learns from thumbs up/down, gets better).
- **Optional mesh (clear upgrade path, never intrusive):** join a pool (scan QR/tap invite),
  create a pool (become hub), share learning (registry samples exchange), shared file exchange
  (opt-in). A small banner: *"Optional: connect with nearby devices."*

## 3. Deliberately Left Out
No forced login/account; no cloud sync unless opted in; no federation management for standalone
users; no developer tools (CLI/scripting/raw mesh); no high-risk industrial controls. Goal: calm,
useful, private from first launch.

## 4. Default Visual Language
Calm muted colours (high contrast outdoors); large text; ≥48dp touch targets; minimal decoration;
clear state (every screen says what it is/doing/next); dark mode by default + light option.
Visual hierarchy: 1) primary action 2) status 3) context 4) secondary actions. One task, one
screen, one clear next step — no 20-widget dashboards.

## 5. Default Layouts by Device
- **Phone (portrait, one-handed):** status bar / context+task (large text) / primary action
  [🎤][✏️][📎] / recent activity / bottom nav [Ask][Tasks][Files][More].
- **Tablet (landscape):** left pane recent activity/task list, right pane current task/assistant
  + context details; consistent bottom nav.
- **Desktop (wide):** top bar (app name | federation context | settings), left sidebar
  (tasks/files/knowledge), main area + context panes, input bar at bottom, status bar (mesh |
  model | privacy). Keyboard shortcuts optional, never required.

## 6. First-Run Experience
Welcome ("Your private, local AI assistant. No cloud. No account. No tracking. [Get started]").
Immediate useful first task (ask something / point at a folder / open voice). Short plain-language
privacy note (stays on device, no server, high-risk always asks first).

## 7. Growth Path to Mesh
Contextual prompt after the user has real value ("If another device had the same assistant, it
could share what it's learned. Create a private pool?"); QR display / scan to join. Never forced;
always framed as private mutual learning.

## 8. Acceptance Criteria
Standalone value (≥3 meaningful tasks with no mesh); privacy (no network calls unless explicitly
enabled); governance (approval + logging); consistency across phone/tablet/desktop;
discoverability (install without docs); mesh as optional upgrade.

## 9. Future Extensions
Native sensors (GPS/camera/thermal/BLE), action-button voice on rugged phones, wearable alerts,
multi-pool management (only after joining 2+ pools), desktop system tray node, app store listing.

## 10. Conclusion
The first app is a quiet, private, useful assistant that happens to be able to join a mesh when
the user is ready. The cage works alone; the mesh is an invitation.
