/* infer/llama_infer.c — embedded inference handler (spec v1, M1).
 *
 * Wraps llama.cpp (system libllama.so + <llama.h>) behind the sp_infer_*
 * API.  CPU-only in Milestone 1: n_gpu_layers is accepted but the
 * shipped path defaults to 0 (Ollama remains the fallback provider).
 *
 * Build:  gcc -O2 -I. -Iinfer infer/llama_infer.c infer/smoke_infer.c -lllama -lm
 *   (NOTE: the flag is -lllama — three L's: -l + llama → libllama.so)
 */
#include "llama_infer.h"
#include "plan_gbnf.h"

#include <llama.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct sp_infer_ctx {
    struct llama_model*    model;
    struct llama_context*  ctx;
    const struct llama_vocab* vocab;
    int n_ctx;
    int n_gpu_layers;
    int n_params_m;
    char model_desc[512];
    /* M2 prefix cache: the static system prompt lives in the KV cache,
     * so every request only decodes the user prompt + generation tail. */
    char* prefix_sys;         /* the cached system prompt text (cache key) */
    llama_pos prefix_len;     /* KV positions [0, prefix_len) hold the prefix */
    int   prefix_valid;
};

/* ── growable byte buffer ──────────────────────────────────────────────── */
typedef struct { char* data; size_t len, cap; } sbuf;

static void sb_init(sbuf* sb) {
    sb->cap = 1024;
    sb->len = 0;
    sb->data = (char*)malloc(sb->cap);
    if (sb->data) sb->data[0] = '\0';
}

static void sb_append(sbuf* sb, const char* s, size_t n) {
    if (!sb->data) return;
    if (sb->len + n + 1 > sb->cap) {
        while (sb->len + n + 1 > sb->cap) sb->cap *= 2;
        char* nd = (char*)realloc(sb->data, sb->cap);
        if (!nd) return;
        sb->data = nd;
    }
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

/* ── lifecycle ─────────────────────────────────────────────────────────── */
sp_infer_ctx* sp_infer_init(const char* model_path, int n_ctx, int n_gpu_layers) {
    if (!model_path || !model_path[0]) {
        fprintf(stderr, "sp_infer: no model path\n");
        return NULL;
    }
    llama_backend_init();

    struct llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = n_gpu_layers > 0 ? n_gpu_layers : 0;

    struct llama_model* model = llama_model_load_from_file(model_path, mp);
    if (!model) {
        fprintf(stderr, "sp_infer: model load failed: %s\n", model_path);
        return NULL;
    }

    struct llama_context_params cp = llama_context_default_params();
    cp.n_ctx = n_ctx > 0 ? n_ctx : 1024;
    /* thread count: conservative default for M1; SP_INFER_N_THREADS overrides */
    {
        const char* nth = getenv("SP_INFER_N_THREADS");
        cp.n_threads = (nth && nth[0]) ? atoi(nth) : 2;
        if (cp.n_threads < 1) cp.n_threads = 1;
    }

    struct llama_context* ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        fprintf(stderr, "sp_infer: context init failed\n");
        llama_model_free(model);
        return NULL;
    }

    sp_infer_ctx* out = (sp_infer_ctx*)calloc(1, sizeof(*out));
    if (!out) {
        llama_free(ctx);
        llama_model_free(model);
        return NULL;
    }
    out->model  = model;
    out->ctx    = ctx;
    out->vocab  = llama_model_get_vocab(model);
    out->n_ctx  = cp.n_ctx;
    out->n_gpu_layers = mp.n_gpu_layers;
    out->n_params_m = (int)(llama_model_n_params(model) / 1000000u);
    out->model_desc[0] = '\0';
    llama_model_desc(model, out->model_desc, sizeof(out->model_desc));
    return out;
}

void sp_infer_free(sp_infer_ctx* ctx) {
    if (!ctx) return;
    free(ctx->prefix_sys);
    if (ctx->ctx)   llama_free(ctx->ctx);
    if (ctx->model) llama_model_free(ctx->model);
    free(ctx);
}

/* ── generation ────────────────────────────────────────────────────────── */
char* sp_infer_generate(sp_infer_ctx* ctx,
                        const char* system_prompt,
                        const char* user_prompt,
                        const char* grammar,
                        int max_tokens,
                        float temperature,
                        int* tokens_generated) {
    if (tokens_generated) *tokens_generated = 0;
    if (!ctx || !ctx->ctx) return NULL;
    if (max_tokens <= 0) max_tokens = 64;

    llama_memory_t mem = llama_get_memory(ctx->ctx);
    int use_prefix = (ctx->prefix_valid && system_prompt &&
                      ctx->prefix_sys && strcmp(ctx->prefix_sys, system_prompt) == 0);

    /* Build the prompt part(s).  With a valid cached prefix we only
     * tokenize the user text and continue the KV sequence at the prefix
     * boundary ("\n\n" separator, same as the full prompt layout). */
    size_t sys_len = system_prompt ? strlen(system_prompt) : 0;
    size_t usr_len = user_prompt   ? strlen(user_prompt)   : 0;

    llama_token* toks = (llama_token*)malloc((size_t)ctx->n_ctx * sizeof(llama_token));
    if (!toks) return NULL;

    int n_toks = 0;
    if (use_prefix) {
        /* drop the previous generation tail, keep [0, prefix_len) */
        if (!llama_memory_seq_rm(mem, 0, ctx->prefix_len, -1)) {
            fprintf(stderr, "sp_infer: prefix tail remove failed; falling back to full path\n");
            use_prefix = 0;
        }
    }
    if (!use_prefix) {
        /* Full path: system + user as one prompt (BOS via add_special). */
        char* prompt = (char*)malloc(sys_len + usr_len + 8);
        if (!prompt) { free(toks); return NULL; }
        size_t q = 0;
        if (sys_len) { memcpy(prompt, system_prompt, sys_len); q += sys_len; }
        if (sys_len && usr_len) { prompt[q++] = '\n'; prompt[q++] = '\n'; }
        if (usr_len) { memcpy(prompt + q, user_prompt, usr_len); q += usr_len; }
        prompt[q] = '\0';
        n_toks = llama_tokenize(ctx->vocab, prompt, (int32_t)q, toks,
                                ctx->n_ctx, true, true);
        free(prompt);
        llama_memory_clear(mem, true);
    } else {
        /* Cached path: only the user text, no BOS (the prefix already has
         * the BOS), positions continue from prefix_len. */
        char* usr_prompt = (char*)malloc(usr_len + 3);
        if (!usr_prompt) { free(toks); return NULL; }
        size_t q = 0;
        usr_prompt[q++] = '\n'; usr_prompt[q++] = '\n';
        if (usr_len) { memcpy(usr_prompt + q, user_prompt, usr_len); q += usr_len; }
        usr_prompt[q] = '\0';
        n_toks = llama_tokenize(ctx->vocab, usr_prompt, (int32_t)q, toks,
                                ctx->n_ctx, false, true);
        free(usr_prompt);
    }
    if (n_toks < 0) { free(toks); fprintf(stderr, "sp_infer: tokenize failed\n"); return NULL; }

    /* First decode: the whole prompt, or the user continuation. */
    struct llama_batch batch;
    if (use_prefix) {
        /* explicit positions: continue the sequence at prefix_len */
        batch = llama_batch_init(n_toks, 0, 1);
        batch.n_tokens = n_toks;
        for (int i = 0; i < n_toks; i++) {
            batch.token[i] = toks[i];
            batch.pos[i]   = ctx->prefix_len + (llama_pos)i;
            batch.n_seq_id[i] = 1;
            batch.seq_id[i][0] = 0;
            batch.logits[i] = (i == n_toks - 1);
        }
    } else {
        batch = llama_batch_get_one(toks, n_toks);
    }
    if (llama_decode(ctx->ctx, batch) < 0) {
        if (use_prefix) llama_batch_free(batch);
        free(toks);
        fprintf(stderr, "sp_infer: decode failed\n");
        return NULL;
    }
    if (use_prefix) llama_batch_free(batch);

    /* Sampler chain: grammar (if any) -> temp -> dist.
     * Grammar MUST precede temp: with temperature 0 the temp sampler
     * collapses logits to a one-hot, and a grammar applied after it can
     * only mask that single token — leaving dist nothing to sample
     * (llama_sampler_dist_apply "found" assertion). */
    struct llama_sampler* smpl =
        llama_sampler_chain_init(llama_sampler_chain_default_params());
    if (!smpl) { free(toks); return NULL; }
    if (grammar && grammar[0]) {
        struct llama_sampler* g =
            llama_sampler_init_grammar(ctx->vocab, grammar, "root");
        if (g) llama_sampler_chain_add(smpl, g);
        else fprintf(stderr, "sp_infer: grammar parse failed, continuing unconstrained\n");
    }
    llama_sampler_chain_add(smpl, llama_sampler_init_temp(temperature));
    llama_sampler_chain_add(smpl, llama_sampler_init_dist(1u));

    llama_token eos = llama_vocab_eos(ctx->vocab);
    sbuf out; sb_init(&out);

    int n_gen = 0;
    long t_start = (long)time(NULL);
    for (int i = 0; i < max_tokens; i++) {
        llama_token id = llama_sampler_sample(smpl, ctx->ctx, -1);
        if (id == eos) break;
        n_gen++;
        if ((n_gen & 15) == 0)
            fprintf(stderr, "sp_infer: gen %d/%d (%lds)\n", n_gen, max_tokens,
                    (long)time(NULL) - t_start);

        char piece[512];
        int len = llama_token_to_piece(ctx->vocab, id, piece, (int32_t)sizeof(piece) - 1,
                                       0, false);
        if (len < 0) len = 0;
        if (len > 0) sb_append(&out, piece, (size_t)len);

        struct llama_batch b1 = llama_batch_get_one(&id, 1);
        if ((n_gen & 15) == 0)
            fprintf(stderr, "sp_infer: decode %d/%d\n", n_gen, max_tokens);
        int dr = llama_decode(ctx->ctx, b1);
        if (dr < 0) { fprintf(stderr, "sp_infer: decode FAILED at %d (%d)\n", n_gen, dr); break; }
        else if (dr > 0) fprintf(stderr, "sp_infer: decode warning at %d (%d)\n", n_gen, dr);
    }

    llama_sampler_free(smpl);
    free(toks);
    if (tokens_generated) *tokens_generated = n_gen;
    return out.data ? out.data : strdup("");
}

/* ── Milestone 2: prefix KV caching ─────────────────────────────────────── */
int sp_infer_cache_prefix(sp_infer_ctx* ctx, const char* prefix) {
    if (!ctx || !ctx->ctx || !prefix || !prefix[0]) return -1;

    /* Tokenize the prefix (BOS via add_special) and decode it once. */
    int max_toks = ctx->n_ctx;
    llama_token* toks = (llama_token*)malloc((size_t)max_toks * sizeof(llama_token));
    if (!toks) return -1;
    int n = llama_tokenize(ctx->vocab, prefix, (int32_t)strlen(prefix), toks,
                           max_toks, true, true);
    if (n < 0) { free(toks); return -1; }

    llama_memory_t mem = llama_get_memory(ctx->ctx);
    llama_memory_clear(mem, true);

    /* No logits needed for the prefix: only the last token's logits would
     * be consumed by a sampler, and generation re-decodes from the user
     * boundary.  Keep them all off (pure fill). */
    struct llama_batch b = llama_batch_init(n, 0, 1);
    b.n_tokens = n;
    for (int i = 0; i < n; i++) {
        b.token[i] = toks[i];
        b.pos[i]   = (llama_pos)i;
        b.n_seq_id[i] = 1;
        b.seq_id[i][0] = 0;
        b.logits[i] = (i == n - 1); /* last one on: sampler needs it if we
                                       ever sample straight after the prefix */
    }
    int ok = llama_decode(ctx->ctx, b) >= 0;
    llama_batch_free(b);
    free(toks);
    if (!ok) {
        fprintf(stderr, "sp_infer: prefix decode failed\n");
        return -1;
    }

    char* keep = strdup(prefix);
    if (!keep) return -1;
    free(ctx->prefix_sys);
    ctx->prefix_sys = keep;
    ctx->prefix_len = (llama_pos)n;
    ctx->prefix_valid = 1;
    fprintf(stderr, "sp_infer: prefix cached (%d tokens)\n", n);
    return 0;
}

void sp_infer_clear_cache(sp_infer_ctx* ctx) {
    if (!ctx) return;
    free(ctx->prefix_sys);
    ctx->prefix_sys = NULL;
    ctx->prefix_len = 0;
    ctx->prefix_valid = 0;
    if (ctx->ctx) llama_memory_clear(llama_get_memory(ctx->ctx), true);
}

int sp_infer_auto_tune(sp_infer_ctx* ctx, int vram_mb,
                       int model_params_m, int n_layers) {
    (void)ctx;
    if (vram_mb <= 0 || n_layers <= 0 || model_params_m <= 0) return 0;
    /* Q4_K_M is ~0.5 bytes/param -> model size in MB.  The 0.15 factor is
     * a conservative per-layer VRAM estimate (spec v1 §8.2). */
    double model_mb = (double)model_params_m * 0.5;
    double per_layer_mb = model_mb * 0.15 / (double)n_layers;
    if (per_layer_mb <= 0.0) return 0;
    int fit = (int)(((double)vram_mb * 0.8) / per_layer_mb);
    return fit < n_layers ? fit : n_layers;
}

const char* sp_infer_model_desc(sp_infer_ctx* ctx) {
    return ctx ? ctx->model_desc : "";
}

int sp_infer_is_available(void) { return 1; }

/* The embedded plan grammar (llama.cpp-safe GBNF). */
const char* sp_infer_plan_gbnf(void) { return SP_PLAN_GBNF; }
