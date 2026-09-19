#ifndef STATEPOD_KERNEL_H
#define STATEPOD_KERNEL_H

/* ================================================================
 * StatePod C Kernel Interface v0.1 (Phase 1 spike)
 *
 * "The kernel executes the context so the orchestrator never has
 *  to read it."
 *
 * C99, zero dependencies, bounded memory (tiny RSS), path-safe.
 * See README.md for design notes and the roadmap.
 * ================================================================ */

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/* ---- Version (open-core: harness verifies the loaded binary) ---- */
#define SP_KERNEL_VERSION "0.3.0"   /* 0.3.0: SP_CONTEXT_SYMBOLIC strategy (doc-layer §6.1) */

/* Per-file read cap; reads past it fail with EFBIG (see sp_read_file). */
#define SP_MAX_FILE_BYTES   (16u*1024u*1024u)
#ifndef SP_GIT_SHA
#define SP_GIT_SHA "dev"
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---- State (opaque) ---- */
typedef struct RepoState RepoState;

const char* sp_version(void);   /* "statepod-kernel <ver> (<sha>)" */

RepoState* sp_state_new(const char* root_path);
void       sp_state_free(RepoState* state);

/* Windows service-management gate (Start/Stop/Restart/Set-Service).
 * Default OFF: the embedding layer must explicitly opt in. */
void sp_state_set_allow_service_mgmt(RepoState* state, int on);
int  sp_state_allow_service_mgmt(const RepoState* state);

/* ---- Operations ---- */
typedef enum {
    SP_OP_READ = 0,      /* read file, optional line range */
    SP_OP_WRITE,         /* write file (creates parent dirs) */
    SP_OP_GREP,          /* POSIX extended regex over a path */
    SP_OP_DIFF,          /* git diff (whole repo or one path) */
    SP_OP_STATUS,        /* git status --porcelain */
    SP_OP_EXECUTE,       /* reserved: whitelisted commands (Phase 2) */
    SP_OP_AST_PARSE,     /* tree-sitter parse; pattern = "" (summary) | "sexp" */
    SP_OP_AST_QUERY,     /* tree-sitter query; pattern = TS query string */
    SP_OP_SYMBOL_SUMMARY /* documentation layer: symbol summaries for a
                          * path (or whole repo); pattern = name substring,
                          * target = exact symbol name */
} SP_OpType;

typedef struct {
    SP_OpType type;
    const char* path;       /* repo-relative path (READ/WRITE/GREP/DIFF) */
    const char* content;    /* new content (WRITE) */
    const char* pattern;    /* POSIX ERE (GREP) */
    const char* target;     /* GREP target override; must be repo-relative */
    const char* command;    /* EXECUTE (reserved) */
    int line_start;         /* READ: 1-based inclusive; 0 = from line 1 */
    int line_end;           /* READ: 1-based inclusive; 0 = to last line */
    int max_results;        /* GREP: cap matches (default 200) */
} SP_Operation;

/* ---- Context strategy (drives what the orchestrator sees) ---- */
typedef enum {
    SP_CONTEXT_DELTA = 0,   /* state summary only (~200 tokens)          */
    SP_CONTEXT_TARGETED,    /* delta + specific files (~1.5k tokens)     */
    SP_CONTEXT_FULL,        /* entire state snapshot (~8k tokens)        */
    SP_CONTEXT_SYMBOLIC     /* symbol summaries instead of raw content   */
                            /* (doc-layer §6.1; ~200-800 bytes)          */
} SP_ContextStrategy;

/* ---- Plan: one batch = many operations ---- */
typedef struct {
    const SP_Operation* ops;
    size_t op_count;
    SP_ContextStrategy context_strategy;
    const char** target_paths;      /* for SP_CONTEXT_TARGETED */
    size_t target_path_count;
    uint64_t max_loops;             /* stuck-loop guard: max consecutive
                                       * identical failing plans before a
                                       * hard error (0 = disabled) */
} SP_Plan;

/* ---- Result ---- */
typedef struct {
    char** logs;                /* human-readable output per op (what the
                                   orchestrator sees) */
    size_t log_count;
    char** touched_files;       /* files modified by this plan */
    size_t touched_file_count;
    char*  state_hash;          /* SHA-256 of event-sourced state */
    int    exit_code;           /* 0 = all ops ok */
    char*  error_message;       /* non-NULL on failure */
    bool   needs_escalation;    /* orchestrator should escalate */
    char*  escalation_reason;   /* why escalation is needed */
} SP_Result;

/* ---- System resource snapshot (Phase 3.5) ----
 * Refreshed on every sp_execute (and at state creation). The kernel
 * owns machine awareness so the orchestrator can decide *how* to act
 * (strategy, escalation) from what the machine can handle, without
 * reading anything itself. */
typedef struct {
    uint64_t mem_total_kb;      /* /proc/meminfo MemTotal          */
    uint64_t mem_avail_kb;      /* MemAvailable (fallback MemFree) */
    uint64_t mem_free_kb;       /* MemFree                         */
    double   load1;             /* /proc/loadavg 1-min average     */
    double   load5;             /* 5-min                           */
    double   load15;            /* 15-min                          */
    uint64_t disk_free_bytes;   /* statvfs f_bavail * f_frsize on root */
    uint64_t disk_total_bytes;  /* f_blocks * f_frsize             */
    int      battery_pct;       /* -1 when no battery present      */
    int      battery_charging;  /* 1 charging, 0 discharging, -1 unknown/none */
    uint64_t uptime_sec;        /* seconds since boot              */
} SP_SystemInfo;

int        sp_sysinfo(RepoState* state, SP_SystemInfo* out);
const char* sp_resource_tag(RepoState* state);

/* ---- Journal hash (SNAP-6) ----
 * Rolling SHA-256 over every audit-ledger event and registry
 * latency/result record. Persisted in the snapshot header so a
 * reload can detect silent journal/registry tampering. */
const char* sp_journal_hash(RepoState* state);
const char* sp_verify_event_chain(RepoState* state);  /* SNAP-7: per-line chain over events.jsonl */

/* ---- Registry timing (SNAP-5) ----
 * Temporal view of one signature aggregated across kernels: average
 * latency, min/max spread, sample counts (mechanical + semantic
 * feedback), and the CLOCK_MONOTONIC ms of the most recent record
 * (latency or feedback). Lets the orchestrator factor time budget and
 * learned strategy success into strategy/confidence decisions. */
typedef struct {
    uint64_t samples;       /* latency samples across all kernels */
    uint64_t fb_samples;    /* semantic feedback samples (sp_record_result) */
    uint64_t avg_us;        /* total_us / samples (0 if none)     */
    uint64_t min_us;        /* 0 when no samples yet              */
    uint64_t max_us;        /* 0 when no samples yet              */
    int64_t  last_ts_ms;    /* monotonic ms of most recent record */
} SP_RegistryStats;

int sp_registry_timing(RepoState* state, const char* signature,
                       SP_RegistryStats* out);

/* ---- Documentation layer (v1: C via tree-sitter, in-file call graph) ---- */
/* Build/refresh the symbol cache for path_filter (NULL = whole repo).
 * Returns the number of symbols indexed, or -1 without tree-sitter. */
int sp_build_symbol_index(RepoState* state, const char* path_filter);

/* Drop cached summaries for the given repo-relative paths (e.g. after
 * out-of-band edits). Returns 0 on success. */
int sp_invalidate_symbols(RepoState* state, const char** paths,
                          size_t path_count);

/* ---- Core functions ---- */
SP_Result* sp_execute(RepoState* state, const SP_Plan* plan);
void       sp_result_free(SP_Result* result);

/* ---- State persistence (Phase 2, minimal version here) ---- */
int        sp_state_save(RepoState* state, const char* path);
RepoState* sp_state_load(const char* path);

/* ---- Lazy file access (orchestrator convenience) ----
 * Returned pointer is owned by `state` and valid until the next
 * call on the same state. NULL on failure. */
const char* sp_read_file(RepoState* state, const char* path);
int         sp_write_file(RepoState* state, const char* path, const char* content);

/* ---- Checkpoint/rollback (Phase 2) ---- */
uint64_t sp_checkpoint(RepoState* state);
int      sp_rollback(RepoState* state, uint64_t checkpoint_id);

/* ---- LoRA management (Phase 4) ----
 * Apply-only hook (stub until the embedded llama.cpp path grows adapter
 * support). No export: there is no trainer in this repo — fine-tuning, if it
 * ever starts, lives in the harness exporting GGUF-LoRA files. */
int sp_apply_lora(RepoState* state, const char* lora_path);

/* ---- Performance registry (Phase 3) ----
 * sp_get_best_kernel returns the best average latency in microseconds
 * (double); sp_best_kernel_name tells the router WHICH kernel won. */
int    sp_record_latency(RepoState* state, const char* signature, const char* kernel, uint64_t latency_us);
int    sp_record_result(RepoState* state, const char* signature, const char* kernel, int ok);
double sp_get_best_kernel(RepoState* state, const char* signature);
double sp_get_success_rate(RepoState* state, const char* signature);
const char* sp_best_kernel_name(RepoState* state, const char* signature);


/* ---- YAML plan export (T-0122 through T-0125) ---- */
char *sp_plan_to_yaml(const SP_Plan *plan, size_t op_buf_size);
int sp_yaml_to_plan(const char *yaml, SP_Plan *plan);
bool sp_yaml_validate(const char *yaml);

#ifdef __cplusplus
}
#endif

#endif /* STATEPOD_KERNEL_H */
