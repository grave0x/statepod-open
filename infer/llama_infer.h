/* infer/llama_infer.h — embedded inference handler (spec v1).
 *
 * Replaces the external Ollama service with a natively embedded
 * llama.cpp runtime: the node loads a GGUF model and calls
 * sp_infer_generate() directly (no HTTP hop, no separate daemon).
 *
 * Milestone 1 ships: init / free / generate (grammar + temperature)
 * CPU-only.  Milestone 2 (prefix caching) and 3 (GPU auto-tune) are
 * honest stubs here and land in follow-up commits.
 */
#ifndef SP_LLAMA_INFER_H
#define SP_LLAMA_INFER_H

#include <stddef.h>

typedef struct sp_infer_ctx sp_infer_ctx;

/* Load a GGUF model.  n_ctx = context size (<=0 -> 1024).
 * n_gpu_layers = layers to offload (0 = CPU only).
 * Returns NULL on failure (message on stderr). */
sp_infer_ctx* sp_infer_init(const char* model_path, int n_ctx, int n_gpu_layers);

/* Free everything. */
void sp_infer_free(sp_infer_ctx* ctx);

/* Generate text.  grammar is a GBNF string or NULL (free-form).
 * Returns a malloc'd null-terminated string (caller frees).
 * tokens_generated (may be NULL) receives the token count.
 * max_tokens is a hard cap. */
char* sp_infer_generate(sp_infer_ctx* ctx,
                        const char* system_prompt,
                        const char* user_prompt,
                        const char* grammar,
                        int max_tokens,
                        float temperature,
                        int* tokens_generated);

/* Milestone 2: prefix KV cache.  Decodes `prefix` (e.g. the static
 * system prompt) into the KV cache once; every sp_infer_generate() with
 * a matching system_prompt then only decodes the user prompt + generation
 * tail (positions continue from the prefix boundary).  Returns 0 on
 * success, -1 on failure.  Implicitly invalidates the previous prefix. */
int sp_infer_cache_prefix(sp_infer_ctx* ctx, const char* prefix);

/* Drop the cached prefix (falls back to the full-prompt path). */
void sp_infer_clear_cache(sp_infer_ctx* ctx);

/* Milestone 3: conservative GPU layer estimate.  vram_mb = available
 * VRAM, model_params_m = model size in millions of params, n_layers =
 * total layers.  Returns a layer count; 0 when no VRAM.  NOTE: applying
 * a new layer count requires a context rebuild (later work). */
int sp_infer_auto_tune(sp_infer_ctx* ctx, int vram_mb,
                       int model_params_m, int n_layers);

/* Model description (llama_model_desc), or "" if not loaded. */
const char* sp_infer_model_desc(sp_infer_ctx* ctx);

/* Non-zero if this build links llama.cpp. */
int sp_infer_is_available(void);

/* The embedded plan GBNF (llama.cpp-safe; same schema as
 * harness/plan_grammar.py PLAN_GBNF). */
const char* sp_infer_plan_gbnf(void);

#endif /* SP_LLAMA_INFER_H */
