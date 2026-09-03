#ifndef SWARMSTATE_KERNEL_H
#define SWARMSTATE_KERNEL_H

/* ================================================================
 * SwarmState C Kernel Interface v0.1 (Phase 1 spike)
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
#define SS_KERNEL_VERSION "0.3.0"   /* 0.3.0: SS_CONTEXT_SYMBOLIC strategy (doc-layer §6.1) */

/* Per-file read cap; reads past it fail with EFBIG (see ss_read_file). */
#define SS_MAX_FILE_BYTES   (16u*1024u*1024u)
#ifndef SS_GIT_SHA
#define SS_GIT_SHA "dev"
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---- State (opaque) ---- */
typedef struct RepoState RepoState;

const char* ss_version(void);   /* "swarmstate-kernel <ver> (<sha>)" */

RepoState* ss_state_new(const char* root_path);
void       ss_state_free(RepoState* state);

/* Windows service-management gate (Start/Stop/Restart/Set-Service).
 * Default OFF: the embedding layer must explicitly opt in. */
void ss_state_set_allow_service_mgmt(RepoState* state, int on);
int  ss_state_allow_service_mgmt(const RepoState* state);

/* ---- Operations ---- */
typedef enum {
    SS_OP_READ = 0,      /* read file, optional line range */
    SS_OP_WRITE,         /* write file (creates parent dirs) */
    SS_OP_GREP,          /* POSIX extended regex over a path */
    SS_OP_DIFF,          /* git diff (whole repo or one path) */
    SS_OP_STATUS,        /* git status --porcelain */
    SS_OP_EXECUTE,       /* reserved: whitelisted commands (Phase 2) */
    SS_OP_AST_PARSE,     /* tree-sitter parse; pattern = "" (summary) | "sexp" */
    SS_OP_AST_QUERY,     /* tree-sitter query; pattern = TS query string */
    SS_OP_SYMBOL_SUMMARY /* documentation layer: symbol summaries for a
                          * path (or whole repo); pattern = name substring,
                          * target = exact symbol name */
} SS_OpType;

typedef struct {
    SS_OpType type;
    const char* path;       /* repo-relative path (READ/WRITE/GREP/DIFF) */
    const char* content;    /* new content (WRITE) */
    const char* pattern;    /* POSIX ERE (GREP) */
    const char* target;     /* GREP target override; must be repo-relative */
    const char* command;    /* EXECUTE (reserved) */
    int line_start;         /* READ: 1-based inclusive; 0 = from line 1 */
    int line_end;           /* READ: 1-based inclusive; 0 = to last line */
    int max_results;        /* GREP: cap matches (default 200) */
} SS_Operation;

/* ---- Context strategy (drives what the orchestrator sees) ---- */
typedef enum {
    SS_CONTEXT_DELTA = 0,   /* state summary only (~200 tokens)          */
    SS_CONTEXT_TARGETED,    /* delta + specific files (~1.5k tokens)     */
    SS_CONTEXT_FULL,        /* entire state snapshot (~8k tokens)        */
    SS_CONTEXT_SYMBOLIC     /* symbol summaries instead of raw content   */
                            /* (doc-layer §6.1; ~200-800 bytes)          */
} SS_ContextStrategy;

/* ---- Plan: one batch = many operations ---- */
typedef struct {
    const SS_Operation* ops;
    size_t op_count;
    SS_ContextStrategy context_strategy;
    const char** target_paths;      /* for SS_CONTEXT_TARGETED */
    size_t target_path_count;
    uint64_t max_loops;             /* stuck-loop guard: max consecutive
                                       * identical failing plans before a
                                       * hard error (0 = disabled) */
} SS_Plan;

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
} SS_Result;

/* ---- System resource snapshot (Phase 3.5) ----
 * Refreshed on every ss_execute (and at state creation). The kernel
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
} SS_SystemInfo;

int        ss_sysinfo(RepoState* state, SS_SystemInfo* out);
const char* ss_resource_tag(RepoState* state);

/* ---- Journal hash (SNAP-6) ----
 * Rolling SHA-256 over every audit-ledger event and registry
 * latency/result record. Persisted in the snapshot header so a
 * reload can detect silent journal/registry tampering. */
const char* ss_journal_hash(RepoState* state);

/* ---- Registry timing (SNAP-5) ----
 * Temporal view of one signature aggregated across kernels: average
 * latency, min/max spread, sample counts (mechanical + semantic
 * feedback), and the CLOCK_MONOTONIC ms of the most recent record
 * (latency or feedback). Lets the orchestrator factor time budget and
 * learned strategy success into strategy/confidence decisions. */
typedef struct {
    uint64_t samples;       /* latency samples across all kernels */
    uint64_t fb_samples;    /* semantic feedback samples (ss_record_result) */
    uint64_t avg_us;        /* total_us / samples (0 if none)     */
    uint64_t min_us;        /* 0 when no samples yet              */
    uint64_t max_us;        /* 0 when no samples yet              */
    int64_t  last_ts_ms;    /* monotonic ms of most recent record */
} SS_RegistryStats;

int ss_registry_timing(RepoState* state, const char* signature,
                       SS_RegistryStats* out);

/* ---- Documentation layer (v1: C via tree-sitter, in-file call graph) ---- */
/* Build/refresh the symbol cache for path_filter (NULL = whole repo).
 * Returns the number of symbols indexed, or -1 without tree-sitter. */
int ss_build_symbol_index(RepoState* state, const char* path_filter);

/* Drop cached summaries for the given repo-relative paths (e.g. after
 * out-of-band edits). Returns 0 on success. */
int ss_invalidate_symbols(RepoState* state, const char** paths,
                          size_t path_count);

/* ---- Core functions ---- */
SS_Result* ss_execute(RepoState* state, const SS_Plan* plan);
void       ss_result_free(SS_Result* result);

/* ---- State persistence (Phase 2, minimal version here) ---- */
int        ss_state_save(RepoState* state, const char* path);
RepoState* ss_state_load(const char* path);

/* ---- Lazy file access (orchestrator convenience) ----
 * Returned pointer is owned by `state` and valid until the next
 * call on the same state. NULL on failure. */
const char* ss_read_file(RepoState* state, const char* path);
int         ss_write_file(RepoState* state, const char* path, const char* content);

/* ---- Checkpoint/rollback (Phase 2) ---- */
uint64_t ss_checkpoint(RepoState* state);
int      ss_rollback(RepoState* state, uint64_t checkpoint_id);

/* ---- LoRA management (Phase 4) ---- */
int ss_apply_lora(RepoState* state, const char* lora_path);
int ss_export_lora(RepoState* state, const char* output_path);

/* ---- Performance registry (Phase 3) ----
 * ss_get_best_kernel returns the best average latency in microseconds
 * (double); ss_best_kernel_name tells the router WHICH kernel won. */
int    ss_record_latency(RepoState* state, const char* signature, const char* kernel, uint64_t latency_us);
int    ss_record_result(RepoState* state, const char* signature, const char* kernel, int ok);
double ss_get_best_kernel(RepoState* state, const char* signature);
double ss_get_success_rate(RepoState* state, const char* signature);
const char* ss_best_kernel_name(RepoState* state, const char* signature);

#ifdef __cplusplus
}
#endif

#endif /* SWARMSTATE_KERNEL_H */
