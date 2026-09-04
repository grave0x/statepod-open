# SwarmState UI wireframes (uxd)

Built with [UXDesign.c](../../UXDesign.c) `uxd` — C99 terminal UX designer.

| File | Screen |
|------|--------|
| `00-msp-flow.uxd` | MSP demo: join → diagnose → remediate |
| `01-field-worker.uxd` | Role view (alerts / tasks / status) |
| `02-supervisor.uxd` | Role view + pending gov approve/deny |
| `03-admin-qr.uxd` | Admin QR invite + status |
| `04-gov-approve.uxd` | Phase-5 target: token approve/deny |
| `05-observer.uxd` | Read-only observer |

```bash
uxd render docs/ux/02-supervisor.uxd
uxd html docs/ux/02-supervisor.uxd /tmp/supervisor.html
# open HTML quietly on an empty Hyprland workspace — do not steal focus
```

Grounded in shipped `win/swarmstate-node.c` widgets + `docs/ui-kit-spec-v1.md`.
Gov approve/deny is **wireframe target** (UI still display-only today).
