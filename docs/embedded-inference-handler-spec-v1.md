# SwarmState Embedded Inference Handler Spec v1.0

**Status:** Draft for implementation  
**Purpose:** Replace the external Ollama service dependency with a natively embedded inference engine, giving SwarmState a single‑binary, local‑first brain with full control over GPU offload, caching, grammar enforcement, and batching.

---

## 1. Goals

- Remove the need to run Ollama as a separate service.
- Embed a high‑performance inference engine directly into the SwarmState node.
- Provide automatic GPU offload tuning based on available hardware and sysinfo.
- Persist prompt prefix state to reduce per‑request token evaluation.
- Enforce plan JSON grammar at the sampler level to eliminate most invalid plans.
- Support batching and model routing when the mesh inference broker is active.
- Keep the kernel's mechanical containment model unchanged.

---

## 2. Non‑goals

- Do not write a transformer inference engine from scratch.
- Do not replace llama.cpp as the underlying runtime.
- Do not add cloud‑only features or require network access for inference.
- Do not change the public `sw` CLI or existing test suites except where explicitly noted.

---

## 3. Underlying Runtime

Use **llama.cpp** as the inference backend, compiled as a static library and linked into the SwarmState node.

- Version: latest stable release at implementation time.
- Quantization: GGUF format, Q4_K_M or Q4_K_S as default.
- Supported models: Qwen2.5‑Coder 1.5B (primary), Qwen2.5‑Coder 7B (optional).

The embedded engine is **not** a server. It is a library that the node calls directly.

---

## 4. API Design

The inference handler lives in a new C module: `infer/llama_infer.c` / `infer/llama_infer.h`.

### 4.1 Initialization

```c
typedef struct ss_infer_ctx ss_infer_ctx;

ss_infer_ctx* ss_infer_init(const char* model_path, int n_ctx, int n_gpu_layers);
void ss_infer_free(ss_infer_ctx* ctx);
```

- `model_path` – path to a GGUF model file.
- `n_ctx` – context size, typically 1024 or 2048.
- `n_gpu_layers` – number of layers to offload to GPU (0 = CPU only). May be tuned later.

### 4.2 Generation

```c
char* ss_infer_generate(
    ss_infer_ctx* ctx,
    const char* system_prompt,
    const char* user_prompt,
    const char* grammar,      // GBNF grammar string or NULL
    int max_tokens,
    float temperature,
    int* tokens_generated
);
```

- Returns a null‑terminated string containing the generated output.
- `grammar` constrains sampling to valid plan JSON when provided.
- `max_tokens` is a hard cap to prevent runaway generation.
- `tokens_generated` is output only and may be NULL.

### 4.3 Caching

```c
int ss_infer_cache_prefix(ss_infer_ctx* ctx, const char* prefix);
void ss_infer_clear_cache(ss_infer_ctx* ctx);
```

- `ss_infer_cache_prefix` stores the KV cache for a given prefix (e.g., the system prompt) so subsequent calls can reuse it.
- The cache is keyed by the string content hash. If the prefix changes, the cache is invalidated.
- `ss_infer_clear_cache` drops all cached prefixes.

### 4.4 GPU Offload Tuning

```c
int ss_infer_auto_tune(ss_infer_ctx* ctx, const ss_sysinfo_t* si);
```

- Inspects available VRAM and current memory pressure.
- Chooses the highest `n_gpu_layers` that fits without causing out‑of‑memory.
- Returns the new layer count.
- The registry may later call this on every system state change.

### 4.5 Batch Inference (v2, optional)

```c
char** ss_infer_batch(ss_infer_ctx* ctx, const char** prompts, int n, const char* grammar, int max_tokens_per, float temperature);
```

- For mesh inference provider mode.
- Batches multiple independent prompts into one forward pass where supported by the backend.

---

## 5. Integration into `swarmstate-node.c`

The Windows node and any future Linux node use this embedded inference engine as the default provider.

### 5.1 Model selection

- If `--model` is given, load that model.
- Otherwise, use the smallest model that is present in the model directory.
- Model directory: `~/.local/share/swarmstate/models` (or `%APPDATA%\SwarmState\models` on Windows).

### 5.2 Inference provider flow

1. Node starts, initialises `ss_infer_ctx` with the default model.
2. When an inference request arrives:
   - Call `ss_infer_generate` with the fixed system prompt, user prompt, and plan grammar.
   - Return the generated JSON as the response.
3. If no model is available or inference fails, fall back to:
   - Ollama if detected and explicitly enabled.
   - Otherwise, return a clean provider‑unavailable error.

### 5.3 Governance

- The embedded inference engine is subject to the same governance as any other op.
- High‑risk actions still require auth + reason before execution, regardless of how the plan was generated.

---

## 6. Prompt Prefix Caching

The system prompt used for plan generation is static for a given model and schema. To minimise latency:

1. On first inference call, split the prompt into `system_prompt` and `user_prompt`.
2. Cache the KV state for `system_prompt` using `ss_infer_cache_prefix`.
3. On subsequent calls, only evaluate the new `user_prompt` tokens.

This reduces prompt evaluation from ~500 tokens to ~15 tokens per task on local models.

---

## 7. Grammar Enforcement

The plan grammar already exists as `harness/plan_grammar.py` (`PLAN_GBNF`).

The embedded engine should:

- Pass `PLAN_GBNF` as the grammar string to `llama.cpp` when generating plans.
- This guarantees the model only emits valid JSON matching the plan schema.
- Invalid plans due to missing fields or malformed JSON are eliminated at the sampler level.

If grammar support is unavailable for a given backend, fall back to the existing strict validator.

---

## 8. GPU Offload Auto‑Tuning

### 8.1 Inputs

- Model size in millions of parameters.
- Available VRAM from `ss_sysinfo`.
- Current memory pressure from the registry or OS.

### 8.2 Algorithm

1. Start with `n_gpu_layers = 0`.
2. Estimate per‑layer VRAM usage as `model_size * 0.15 / n_layers`.
3. While estimated VRAM usage < available VRAM * 0.8, increase `n_gpu_layers`.
4. Cap at total number of layers.
5. Apply the resulting `n_gpu_layers` via `ss_infer_auto_tune`.

This is a conservative default. The registry can later learn better settings per task signature and hardware.

---

## 9. Model Routing

The inference handler is model‑aware. The existing `pick_model_rates` logic in the orchestrator can call `ss_infer_generate` with the chosen model by loading/unloading as needed.

To keep initial scope small:

- v1: single model loaded at startup.
- v2: swap models on demand using the registry's proven model routing.

---

## 10. Build and Packaging

- Add llama.cpp as a submodule or vendored dependency.
- Build llama.cpp as a static library for each target platform:
  - Linux x86_64
  - Linux ARM64
  - Windows x86_64 (mingw, static)
- Link the static library into `swarmstate-node.c`.
- Do not modify the core kernel safety invariants.

The Windows zip should include the node binary with llama.cpp already linked. No separate inference server.

---

## 11. Testing

### 11.1 Unit tests

- `ss_infer_init` loads a tiny test model.
- `ss_infer_generate` returns non‑empty string with grammar.
- Prefix cache reduces token evaluation time on second call.
- `ss_infer_auto_tune` returns sensible layer counts for different VRAM sizes.

### 11.2 Integration tests

- 3‑node live acceptance with embedded inference instead of Ollama.
- Strict grammar negative probe: malformed plan requests are rejected.
- Resource snapshot shows lower memory usage than running Ollama separately.

### 11.3 Cross‑platform

- Linux x86_64 build passes full suite.
- Windows build under Wine passes basic node startup and plan generation.
- ARM64 cross‑compile succeeds.

---

## 12. Milestones

### Milestone 1: CPU‑only embedded inference

- Link llama.cpp into the node.
- Load Qwen2.5‑Coder 1.5B.
- Serve one plan request with grammar enforcement.
- Measure latency vs Ollama.

### Milestone 2: Prefix caching

- Cache the system prompt KV state.
- Demonstrate reduced prompt evaluation time on repeated tasks.
- Add regression test.

### Milestone 3: GPU offload auto‑tuning

- Implement `ss_infer_auto_tune`.
- Test on a machine with a small GPU (e.g., 2GB).
- Show that offload improves tokens/sec without OOM.

### Milestone 4: Model swapping and batching

- Load/unload models on demand.
- Serve batched inference requests in provider mode.
- Integrate with mesh inference broker.

---

## 13. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| llama.cpp API changes | Pin to a specific commit; wrap behind `ss_infer` API. |
| Model loading memory spikes | Use memory‑mapped weights; unload when idle. |
| Grammar enforcement limits model creativity | Only apply for plan JSON, not open‑ended chat. |
| GPU offload causes instability on some hardware | Default to CPU; offload only if auto‑tune proves stable. |
| Static llama.cpp increases binary size | Accept the trade; document the size change. |

---

## 14. Acceptance Criteria

| Criterion | Description |
|-----------|-------------|
| **No Ollama required** | A fresh node starts and serves inference without a separate service. |
| **Grammar enforced** | All generated plans are valid JSON matching the plan schema. |
| **Prefix caching works** | Second call with same system prompt shows measurable reduction in evaluation time. |
| **GPU offload auto‑tunes** | On a supported GPU, layer count is selected automatically without OOM. |
| **Cross‑platform** | Linux and Windows builds run the same inference path. |
| **Fallback** | If embedded inference fails, node returns a clean error or falls back to Ollama if explicitly configured. |

---

## 15. Future Extensions

- Speculative decoding with a small draft model.
- LoRA adapter hot‑swapping per task family.
- Per‑node inference cache shared across mesh peers.
- Persistent KV cache on disk for fast cold starts.
- Battery‑aware offload policies for mobile nodes.
