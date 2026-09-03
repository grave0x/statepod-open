# infer/ — embedded inference handler (spec v1)

Replaces the external Ollama service with llama.cpp linked into the node.

## Milestone 1 (this commit): CPU-only generation

- `llama_infer.h` / `llama_infer.c` — the `ss_infer_*` API (init, free,
  generate with GBNF grammar + temperature; auto_tune heuristic; cache
  stubs for M2).
- `smoke_infer.c` — acceptance smoke test: load a GGUF, generate one
  grammar-constrained plan, one free-form reply, print latency.
- `plan.gbnf` — the plan schema grammar extracted from
  `harness/plan_grammar.py` (`PLAN_GBNF`, root symbol `root`).

  **llama.cpp GBNF compatibility:** the harness `PLAN_GBNF` is formatted
  for humans (multi-line `|` continuations) and uses underscore rule names
  (`op_type`, `line_start`, ...). llama.cpp's grammar parser requires
  single-line rules and rejects underscores in rule names, so `plan.gbnf`
  is the flattened, underscore-free derivative (`optype`, `linestart`, ...).

## Build

    ./infer/build.sh          # defaults to Clang (LLVM toolchain)
    CC=gcc ./infer/build.sh   # override if needed

“LLVM” here means the **Clang/LLVM compiler** used to build the smoke
binary and (optionally) the node embed path — the **runtime** is still
llama.cpp (`libllama`).

Links the **system** llama.cpp runtime (Arch package `llama.cpp`):
`/usr/lib/libllama.so` + `<llama.h>`.  The linker flag is `-lllama`
(three L's = `-l` + `llama`).

## Run

    SS_INFER_N_THREADS=2 nice -n 19 ./infer/smoke_infer \
        ~/.ollama/models/blobs/<1.5b-gguf-sha> infer/plan.gbnf 256 48

Model directory (spec v1 §5.1): `~/.local/share/swarmstate/models`.

## Status

- M1 (CPU-only): implemented + node round-trip VERIFIED 2026-09-02.
  `win/swarmstate-node.c --embed` runs embedded-first with Ollama
  fallback (spec §5.2); generation is serialized on the model-init
  thread via a request queue.  Test: `infer/node_test_client.py`
  (TCP `req` -> grammar-constrained plan -> compacted single-line
  `resp`).  Requires llama-cpp >= 0.3.0 (see Build above).
- M2 (prefix KV cache): implemented + verified 2026-09-02.
  `ss_infer_cache_prefix(ctx, sys)` decodes the static system prompt
  once; `ss_infer_generate()` with a matching system_prompt continues
  the KV sequence at the prefix boundary (only the user prompt +
  generation tail are decoded).  The node caches `SS_PLAN_SYS` at model
  load (~105 tokens); round-trip latency dropped ~2x (13.9s -> 6.7s on
  the same query).  Fallback to the full-prompt path is automatic.
- M3 (GPU auto-tune): `ss_infer_auto_tune` heuristic shipped; runtime
  layer switching needs a context rebuild.
