//! FFI bindings to libswarmstate (swarmstate.h).
//!
//! T-0042: skeleton. Manual `extern "C"` block matching the C99 header —
//! bindgen is overkill for a 12-function surface and would add a build-step
//! dependency. The C header is the source of truth.
//!
//! All `*mut c_char` returns are owned by the kernel — they MUST be copied
//! into Rust `CString` (via `to_str().unwrap_or("")` etc.) before any other
//! kernel call, because the next call can invalidate them. The `*mut c_char`
//! in `SS_Result` is returned to us and is freed via `ss_result_free`.

#![allow(non_camel_case_types, non_snake_case, clippy::missing_safety_doc)]

use std::os::raw::{c_char, c_int};

// ─── Opaque state ────────────────────────────────────────────────────────────

/// Opaque kernel state handle.
#[repr(C)]
pub struct RepoState {
    _opaque: [u8; 0],
}

// ─── Enums (must match kernel.h) ────────────────────────────────────────────

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SS_OpType {
    READ = 0,
    WRITE,
    GREP,
    DIFF,
    STATUS,
    EXECUTE,
    AST_PARSE,
    AST_QUERY,
    SYMBOL_SUMMARY,
}

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SS_ContextStrategy {
    DELTA = 0,
    TARGETED,
    FULL,
    SYMBOLIC,
}

// ─── Plain structs (must match kernel.h layout exactly) ─────────────────────

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct SS_Operation {
    pub op_type:    SS_OpType,
    pub path:       *const c_char,
    pub content:    *const c_char,
    pub pattern:    *const c_char,
    pub target:     *const c_char,
    pub command:    *const c_char,
    pub line_start: c_int,
    pub line_end:   c_int,
    pub max_results: c_int,
}

#[repr(C)]
pub struct SS_Plan {
    pub ops:              *const SS_Operation,
    pub op_count:         usize,
    pub context_strategy: SS_ContextStrategy,
    pub target_paths:     *const *const c_char,
    pub target_path_count: usize,
    pub max_loops:        u64,
}

#[repr(C)]
pub struct SS_Result {
    pub logs:              *mut *mut c_char,
    pub log_count:         usize,
    pub touched_files:     *mut *mut c_char,
    pub touched_file_count: usize,
    pub state_hash:        *mut c_char,
    pub exit_code:         c_int,
    pub error_message:     *mut c_char,
    pub needs_escalation:  bool,
    pub escalation_reason: *mut c_char,
}

#[repr(C)]
#[derive(Debug, Clone, Default)]
pub struct SS_SystemInfo {
    pub mem_total_kb:     u64,
    pub mem_avail_kb:     u64,
    pub mem_free_kb:      u64,
    pub load1:            f64,
    pub load5:            f64,
    pub load15:           f64,
    pub disk_free_bytes:  u64,
    pub disk_total_bytes: u64,
    pub battery_pct:      c_int,
    pub battery_charging: c_int,
    pub uptime_sec:       u64,
}

#[repr(C)]
#[derive(Debug, Clone, Default)]
pub struct SS_RegistryStats {
    pub samples:    u64,
    pub fb_samples: u64,
    pub avg_us:     u64,
    pub min_us:     u64,
    pub max_us:     u64,
    pub last_ts_ms: i64,
}

// ─── extern "C" block ──────────────────────────────────────────────────────

#[link(name = "swarmstate", kind = "dylib")]
extern "C" {
    // Version
    pub fn ss_version() -> *const c_char;

    // State lifecycle
    pub fn ss_state_new(root_path: *const c_char) -> *mut RepoState;
    pub fn ss_state_free(state: *mut RepoState);
    pub fn ss_state_set_allow_service_mgmt(state: *mut RepoState, on: c_int);
    pub fn ss_state_allow_service_mgmt(state: *const RepoState) -> c_int;

    // System info
    pub fn ss_sysinfo(state: *mut RepoState, out: *mut SS_SystemInfo) -> c_int;
    pub fn ss_resource_tag(state: *mut RepoState) -> *const c_char;

    // Journal / registry timing
    pub fn ss_journal_hash(state: *mut RepoState) -> *const c_char;
    pub fn ss_registry_timing(
        state: *mut RepoState,
        signature: *const c_char,
        out: *mut SS_RegistryStats,
    ) -> c_int;

    // Documentation layer
    pub fn ss_build_symbol_index(
        state: *mut RepoState,
        path_filter: *const c_char,
    ) -> c_int;
    pub fn ss_invalidate_symbols(
        state: *mut RepoState,
        paths: *const *const c_char,
        path_count: usize,
    ) -> c_int;

    // Core execution
    pub fn ss_execute(state: *mut RepoState, plan: *const SS_Plan) -> *mut SS_Result;
    pub fn ss_result_free(result: *mut SS_Result);

    // State persistence
    pub fn ss_state_save(state: *mut RepoState, path: *const c_char) -> c_int;
    pub fn ss_state_load(path: *const c_char) -> *mut RepoState;

    // Lazy file access (caller must copy the returned string before next call)
    pub fn ss_read_file(state: *mut RepoState, path: *const c_char) -> *const c_char;
    pub fn ss_write_file(
        state: *mut RepoState,
        path: *const c_char,
        content: *const c_char,
    ) -> c_int;

    // Checkpoint / rollback
    pub fn ss_checkpoint(state: *mut RepoState) -> u64;
    pub fn ss_rollback(state: *mut RepoState, checkpoint_id: u64) -> c_int;

    // LoRA / registry
    pub fn ss_apply_lora(state: *mut RepoState, lora_path: *const c_char) -> c_int;
    pub fn ss_record_latency(
        state: *mut RepoState,
        signature: *const c_char,
        kernel: *const c_char,
        latency_us: u64,
    ) -> c_int;
    pub fn ss_record_result(
        state: *mut RepoState,
        signature: *const c_char,
        kernel: *const c_char,
        ok: c_int,
    ) -> c_int;
    pub fn ss_get_best_kernel(state: *mut RepoState, signature: *const c_char) -> f64;
    pub fn ss_get_success_rate(state: *mut RepoState, signature: *const c_char) -> f64;
    pub fn ss_best_kernel_name(state: *mut RepoState, signature: *const c_char) -> *const c_char;

    // YAML plan export
    pub fn ss_plan_to_yaml(plan: *const SS_Plan, op_buf_size: usize) -> *mut c_char;
    pub fn ss_yaml_to_plan(yaml: *const c_char, plan: *mut SS_Plan) -> c_int;
    pub fn ss_yaml_validate(yaml: *const c_char) -> bool;
}

// ─── Safe wrappers (skeleton — full API added in T-0043) ───────────────────

/// Returns the kernel version string. Safe to call without state.
pub fn version() -> String {
    unsafe {
        let ptr = ss_version();
        if ptr.is_null() {
            return String::new();
        }
        // Kernel returns a static string — no free needed.
        std::ffi::CStr::from_ptr(ptr)
            .to_string_lossy()
            .into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_returns_nonempty() {
        let v = version();
        assert!(!v.is_empty(), "ss_version() returned empty");
        assert!(
            v.contains("swarmstate-kernel"),
            "ss_version() doesn't look like a swarmstate kernel string: {:?}",
            v
        );
    }

    #[test]
    fn libswarmstate_loads() {
        // Just calling version() already links the .so. Verify symbol count > 0
        // by trying a no-op signature lookup.
        unsafe {
            let state = ss_state_new(std::ptr::null());
            // NULL root_path: kernel should reject gracefully and return null
            // OR return a sentinel — both are acceptable for the skeleton test.
            if !state.is_null() {
                let mut info = SS_SystemInfo::default();
                let rc = ss_sysinfo(state, &mut info);
                // rc may be 0 or -1 depending on the root path; we just want
                // the call to not segfault.
                let _ = rc;
                ss_state_free(state);
            }
        }
    }
}
