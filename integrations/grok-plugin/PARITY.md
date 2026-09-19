# StatePod: prime vs Grok harness parity

| Capability | prime (`statepod.ts`) | Grok plugin v0.2 |
|------------|-------------------------|------------------|
| Toggle `~/.statepod/omp.json` | ✅ | ✅ shared |
| Kernel status sample | ✅ before each LLM call | ✅ statusline.sh + `/statepod status` |
| One-shot guidance | ✅ `before_agent_start` systemPrompt | ✅ PreToolUse `additionalContext` once/session |
| Rewrite dump **inputs** | n/a (contains results) | ✅ PreToolUse `updatedInput` (read/shell/grep/lean-ctx) |
| Rewrite tool **results** | ✅ `context` event swaps digest | ❌ **hard gap** — PostToolUse stdout ignored; no result rewrite API |
| Archive oversized output | ✅ outbox | ✅ PostToolUse side-effect → `~/.statepod/grok/outbox/` |
| Containment ledger / stats | ✅ | ✅ same accounting (archives even when model still saw full text once) |
| `/statepod full <id>` | ✅ | ✅ |
| Deny dangerous dumps | optional | PreToolUse can `deny` (not default — rewrite preferred) |

## What “full wiring” means on Grok

1. **Prevent dumps** (PreToolUse) — primary containment.
2. **Archive what slips through** (PostToolUse) — human + `full` + stats.
3. **Guide the model** (one-shot additionalContext).
4. **Surface kernel health** (statusline / status).

True DELTA digests *in the model’s context window* still require prime’s
`context` hook or lean-ctx compression. Prefer both: this plugin + lean-ctx.

## Sampler / tool conflict note

llama.cpp cannot combine custom PLAN_GBNF with auto tool-GBNF (single slot).
That is an inference concern, not a Grok-hook concern — `statepod ask` embed path
keeps plan GBNF; Grok’s own tool loop is unconstrained JSON.
