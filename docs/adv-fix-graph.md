# Adv-review fix graph

Work topologically. Parallel ready leaves: **C1 ‖ C2 ‖ C4 ‖ M1**.

```mermaid
flowchart TD
  E[Epic hardening]
  C1[C1 gov issue mint localhost]
  C2[C2/C3 join_ok member_added auth]
  C4[C4 gov_ui no default secret]
  H2[H2 default-deny empty allow]
  H3[H3 LoRa PSK fail-closed]
  H45[H4/H5 per-auth + enforce]
  M1[M1 find write denylist]
  D[Docs claims / MSP language]
  E --> C1 & C2 & C4 & M1
  C2 --> H2 & H3
  C4 --> H45
  C1 & C2 & H2 --> D
```

| ID | Status | Files |
|----|--------|-------|
| C1 | **done** | `win/swarmstate-node.c` — loopback-only mint |
| C2 | **done** | node `join_hub` + meshd `_join_sock` / allowlisted `member_added` |
| C4 | **done** | `gov_ui.py` — no default secret; bind `127.0.0.1` |
| H2 | **done** | empty allow = deny (`--demo` / `--open-mesh` to open) |
| H3 | **done** | `LoRaLink(require_psk=True)` / `SS_LORA_REQUIRE_PSK` |
| H45 | skipped | per-auth secrets — add when multi-secret deploy needed |
| M1 | **done** | `kernel.c` find `-fprintf/-fprint/-fls` |
| Docs | partial | deep-research folded; MSP/claim rewrite still open |

Check: `harness/test_adv_control.py` + pool/join tests OK.
