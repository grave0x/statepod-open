/* infer/smoke_infer.c — Milestone 1 acceptance smoke test.
 *
 * Loads a GGUF model through ss_infer_*, then:
 *   1. generates one plan request constrained by a GBNF grammar (a file
 *      passed as argv[2], or an inline minimal grammar if "-"),
 *   2. generates a free-form reply to measure tokens/sec,
 *   3. prints latency so it can be compared against the Ollama path.
 *
 * Usage: smoke_infer <model.gguf> [grammar.gbnf|-] [n_ctx] [max_tokens] [sys] [usr]
 */
#include "llama_infer.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

/* Minimal grammar proving the grammar sampler is wired: forces
 * {"a": <digits>} and nothing else. */
static const char* MIN_GRAMMAR =
    "root    ::= \"{\" ws \"\\\"a\\\"\" ws \":\" ws number ws \"}\"\n"
    "number  ::= [0-9]+\n"
    "ws      ::= [ \\t\\n]*\n";

int main(int argc, char** argv) {
    setvbuf(stdout, NULL, _IONBF, 0);  /* logs live: no block buffering */
    if (argc < 2) {
        fprintf(stderr, "usage: %s <model.gguf> [grammar.gbnf|-] [n_ctx] [max_tokens]\n", argv[0]);
        return 2;
    }
    const char* model = argv[1];
    const char* grammar_file = argc > 2 ? argv[2] : "-";
    int n_ctx = argc > 3 ? atoi(argv[3]) : 512;
    int max_tokens = argc > 4 ? atoi(argv[4]) : 64;

    printf("ss_infer available: %d\n", ss_infer_is_available());
    ss_infer_ctx* ctx = ss_infer_init(model, n_ctx, 0);
    if (!ctx) { fprintf(stderr, "FAIL: ss_infer_init\n"); return 1; }
    printf("model: %s\n", ss_infer_model_desc(ctx));
    printf("n_ctx=%d (CPU only)\n", n_ctx);

    /* grammar: file, or inline minimal, or none */
    char* grammar = NULL;
    if (strcmp(grammar_file, "-") == 0) {
        grammar = strdup(MIN_GRAMMAR);
        printf("grammar: inline minimal ({\"a\":<digits>})\n");
    } else {
        FILE* f = fopen(grammar_file, "r");
        if (f) {
            fseek(f, 0, SEEK_END);
            long sz = ftell(f); fseek(f, 0, SEEK_SET);
            grammar = (char*)malloc((size_t)sz + 1);
            if (fread(grammar, 1, (size_t)sz, f) != (size_t)sz) { /* partial */ }
            grammar[sz] = '\0';
            fclose(f);
            printf("grammar: %s (%ld bytes)\n", grammar_file, sz);
        } else {
            printf("grammar: (none — file not found)\n");
        }
    }

    const char* SYS = argc > 5 ? argv[5]
        : "You are SwarmState's plan generator. Emit only valid JSON.";
    const char* USR = argc > 6 ? argv[6]
        : "Generate a plan to read README.md using strategy DELTA.";

    /* M2: cache the system prompt in the KV cache (both runs below reuse
     * it; the free-form run's latency shows the prefix-reuse win). */
    if (ss_infer_cache_prefix(ctx, SYS) != 0)
        printf("prefix cache: FAILED (continuing on the full-prompt path)\n");
    else
        printf("prefix cache: OK (first generate() will reuse it)\n");

    /* 1. grammar-constrained plan */
    int tok1 = 0;
    long t0 = now_ms();
    char* out1 = ss_infer_generate(ctx, SYS, USR, grammar, max_tokens, 0.0f, &tok1);
    long t1 = now_ms();
    printf("\n── grammar-constrained generation ──\n");
    printf("tokens=%d elapsed=%ldms %.1f tok/s\n", tok1, t1 - t0,
           tok1 > 0 ? tok1 * 1000.0 / (t1 - t0) : 0.0);
    printf("output: %s\n", out1 ? out1 : "(null)");
    free(out1);

    /* 2. free-form (no grammar) for throughput */
    int tok2 = 0;
    long t2 = now_ms();
    char* out2 = ss_infer_generate(ctx, SYS, USR, NULL, max_tokens, 0.0f, &tok2);
    long t3 = now_ms();
    printf("\n── free-form generation (no grammar) ──\n");
    printf("tokens=%d elapsed=%ldms %.1f tok/s\n", tok2, t3 - t2,
           tok2 > 0 ? tok2 * 1000.0 / (t3 - t2) : 0.0);
    printf("output: %s\n", out2 ? out2 : "(null)");
    free(out2);

    free(grammar);
    ss_infer_free(ctx);
    printf("\nOK\n");
    return 0;
}
