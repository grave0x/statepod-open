"""SwarmState Python binding — ctypes, Phase 1 spike.

Loads libswarmstate.so (build with `make` in the repo root) and
exposes the C kernel API as a small, typed Python class.

    from swarmstate import SwarmState, SS_CONTEXT_DELTA
    with SwarmState("~/repo") as s:
        s.write("a.c", "int main(void) { return 0; }\\n")
        print(s.grep("main"))
"""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path

# ------------------------------------------------------------------
# Library loading
# ------------------------------------------------------------------
_LIB_CANDIDATES = [
    os.environ.get("SWARMSTATE_LIB", ""),
    str(Path(__file__).resolve().parent.parent / "libswarmstate.so"),
    "libswarmstate.so",
]


def _load_library() -> C.CDLL:
    for cand in _LIB_CANDIDATES:
        if not cand:
            continue
        try:
            lib = C.CDLL(cand)
            lib._ss_lib_path = cand
            return lib
        except OSError:
            continue
    raise OSError(
        "libswarmstate.so not found — run `make` in the SwarmState repo root "
        "(or set SWARMSTATE_LIB)"
    )


lib = _load_library()

# ------------------------------------------------------------------
# Kernel version (open-core: fail loudly on binary/harness drift)
# ------------------------------------------------------------------
lib.ss_version.restype = C.c_char_p
KERNEL_VERSION = (lib.ss_version() or b"").decode()
_EXPECT = os.environ.get("SS_EXPECT_KERNEL", "")
if _EXPECT and _EXPECT not in KERNEL_VERSION:
    raise RuntimeError(
        f"SwarmState kernel version mismatch: expected {_EXPECT!r} but "
        f"loaded {KERNEL_VERSION!r} from {lib._ss_lib_path}. "
        "Rebuild with `make` or point SWARMSTATE_LIB at a matching binary."
    )

# ------------------------------------------------------------------
# Enums (mirror kernel.h)
# ------------------------------------------------------------------
SS_OP_READ = 0
SS_OP_WRITE = 1
SS_OP_GREP = 2
SS_OP_DIFF = 3
SS_OP_STATUS = 4
SS_OP_EXECUTE = 5
SS_OP_AST_PARSE = 6
SS_OP_AST_QUERY = 7
SS_OP_SYMBOL_SUMMARY = 8

_OP_TYPES = {
    "READ": SS_OP_READ, "WRITE": SS_OP_WRITE, "GREP": SS_OP_GREP,
    "DIFF": SS_OP_DIFF, "STATUS": SS_OP_STATUS, "EXECUTE": SS_OP_EXECUTE,
    "AST_PARSE": SS_OP_AST_PARSE, "AST_QUERY": SS_OP_AST_QUERY,
    "SYMBOL_SUMMARY": SS_OP_SYMBOL_SUMMARY,
}

SS_CONTEXT_DELTA = 0
SS_CONTEXT_TARGETED = 1
SS_CONTEXT_FULL = 2
SS_CONTEXT_SYMBOLIC = 3

# ------------------------------------------------------------------
# Structs (must match kernel.h exactly)
# ------------------------------------------------------------------
class SS_Operation(C.Structure):
    _fields_ = [
        ("type", C.c_int),
        ("path", C.c_char_p),
        ("content", C.c_char_p),
        ("pattern", C.c_char_p),
        ("target", C.c_char_p),
        ("command", C.c_char_p),
        ("line_start", C.c_int),
        ("line_end", C.c_int),
        ("max_results", C.c_int),
    ]


class SS_Plan(C.Structure):
    _fields_ = [
        ("ops", C.POINTER(SS_Operation)),
        ("op_count", C.c_size_t),
        ("context_strategy", C.c_int),
        ("target_paths", C.POINTER(C.c_char_p)),
        ("target_path_count", C.c_size_t),
        ("max_loops", C.c_uint64),
    ]


class SS_Result(C.Structure):
    _fields_ = [
        ("logs", C.POINTER(C.c_char_p)),
        ("log_count", C.c_size_t),
        ("touched_files", C.POINTER(C.c_char_p)),
        ("touched_file_count", C.c_size_t),
        ("state_hash", C.c_char_p),
        ("exit_code", C.c_int),
        ("error_message", C.c_char_p),
        ("needs_escalation", C.c_bool),
        ("escalation_reason", C.c_char_p),
    ]

class SS_RegistryStats(C.Structure):
    _fields_ = [
        ("samples", C.c_uint64),
        ("fb_samples", C.c_uint64),
        ("avg_us", C.c_uint64),
        ("min_us", C.c_uint64),
        ("max_us", C.c_uint64),
        ("last_ts_ms", C.c_int64),
    ]


class SS_SystemInfo(C.Structure):
    _fields_ = [
        ("mem_total_kb", C.c_uint64),
        ("mem_avail_kb", C.c_uint64),
        ("mem_free_kb", C.c_uint64),
        ("load1", C.c_double),
        ("load5", C.c_double),
        ("load15", C.c_double),
        ("disk_free_bytes", C.c_uint64),
        ("disk_total_bytes", C.c_uint64),
        ("battery_pct", C.c_int),
        ("battery_charging", C.c_int),
        ("uptime_sec", C.c_uint64),
    ]


# ------------------------------------------------------------------
# Function prototypes
# ------------------------------------------------------------------
lib.ss_state_new.argtypes = [C.c_char_p]
lib.ss_state_new.restype = C.c_void_p
lib.ss_state_free.argtypes = [C.c_void_p]
lib.ss_state_free.restype = None
lib.ss_sysinfo.argtypes = [C.c_void_p, C.POINTER(SS_SystemInfo)]
lib.ss_sysinfo.restype = C.c_int
lib.ss_resource_tag.argtypes = [C.c_void_p]
lib.ss_resource_tag.restype = C.c_char_p
lib.ss_journal_hash.argtypes = [C.c_void_p]
lib.ss_journal_hash.restype = C.c_char_p
lib.ss_registry_timing.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(SS_RegistryStats)]
lib.ss_registry_timing.restype = C.c_int
lib.ss_build_symbol_index.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_build_symbol_index.restype = C.c_int
lib.ss_invalidate_symbols.argtypes = [C.c_void_p, C.POINTER(C.c_char_p), C.c_size_t]
lib.ss_invalidate_symbols.restype = C.c_int
lib.ss_execute.argtypes = [C.c_void_p, C.POINTER(SS_Plan)]
lib.ss_execute.restype = C.POINTER(SS_Result)
lib.ss_result_free.argtypes = [C.POINTER(SS_Result)]
lib.ss_result_free.restype = None
lib.ss_state_save.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_state_save.restype = C.c_int
lib.ss_state_load.argtypes = [C.c_char_p]
lib.ss_state_load.restype = C.c_void_p
lib.ss_read_file.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_read_file.restype = C.c_char_p
lib.ss_write_file.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p]
lib.ss_write_file.restype = C.c_int
lib.ss_checkpoint.argtypes = [C.c_void_p]
lib.ss_checkpoint.restype = C.c_uint64
lib.ss_rollback.argtypes = [C.c_void_p, C.c_uint64]
lib.ss_rollback.restype = C.c_int
lib.ss_apply_lora.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_apply_lora.restype = C.c_int
lib.ss_export_lora.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_export_lora.restype = C.c_int
lib.ss_record_latency.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_uint64]
lib.ss_record_latency.restype = C.c_int
lib.ss_get_best_kernel.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_get_best_kernel.restype = C.c_double
lib.ss_get_success_rate.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_get_success_rate.restype = C.c_double
lib.ss_record_result.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int]
lib.ss_record_result.restype = C.c_int
lib.ss_best_kernel_name.argtypes = [C.c_void_p, C.c_char_p]
lib.ss_best_kernel_name.restype = C.c_char_p

# ------------------------------------------------------------------
# Result wrapper
# ------------------------------------------------------------------
class PlanResult:
    """What the kernel returned for one batch plan."""

    __slots__ = (
        "logs", "touched_files", "state_hash", "exit_code",
        "error_message", "needs_escalation", "escalation_reason",
    )

    def __init__(self, logs, touched_files, state_hash, exit_code,
                 error_message, needs_escalation, escalation_reason):
        self.logs = logs
        self.touched_files = touched_files
        self.state_hash = state_hash
        self.exit_code = exit_code
        self.error_message = error_message
        self.needs_escalation = needs_escalation
        self.escalation_reason = escalation_reason

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        return (
            f"PlanResult(ok={self.ok}, exit_code={self.exit_code}, "
            f"logs={len(self.logs)}, touched={self.touched_files}, "
            f"needs_escalation={self.needs_escalation})"
        )


def _decode(b):
    return b.decode("utf-8", "replace") if b is not None else None


def _parse_result(ptr) -> PlanResult:
    r = ptr.contents
    logs = [_decode(r.logs[i]) for i in range(r.log_count)]
    touched = [_decode(r.touched_files[i]) for i in range(r.touched_file_count)]
    pr = PlanResult(
        logs=logs,
        touched_files=touched,
        state_hash=_decode(r.state_hash),
        exit_code=r.exit_code,
        error_message=_decode(r.error_message),
        needs_escalation=bool(r.needs_escalation),
        escalation_reason=_decode(r.escalation_reason),
    )
    lib.ss_result_free(ptr)
    return pr


def _b(s):
    """str|bytes|None -> c_char_p-friendly bytes or None."""
    if s is None:
        return None
    return os.fsencode(s) if isinstance(s, str) else bytes(s)


# ------------------------------------------------------------------
# Main class
# ------------------------------------------------------------------
class SwarmState:
    """A repo-scoped handle to the C kernel."""

    def __init__(self, root: str):
        self._ptr = lib.ss_state_new(_b(root))
        if not self._ptr:
            raise ValueError(
                f"ss_state_new failed for {root!r}: must be an existing directory "
                "without single quotes in its path"
            )
        self._closed = False

    # -- lifecycle ---------------------------------------------------
    def close(self):
        if not self._closed:
            lib.ss_state_free(self._ptr)
            self._closed = True

    def _check(self):
        """Raise if the kernel handle was already freed (use-after-free
        guard for the lazy-access methods)."""
        if self._closed:
            raise RuntimeError("SwarmState is closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- batch execution ----------------------------------------------
    def execute(self, ops, strategy=SS_CONTEXT_TARGETED,
                target_paths=None, max_loops=0) -> PlanResult:
        """ops: list of dicts with keys type/path/content/pattern/target/
        command/line_start/line_end/max_results.

        max_loops: stuck-loop guard — hard error after this many consecutive
        identical failing plans (0 = disabled)."""
        if self._closed:
            raise RuntimeError("state closed")
        n = len(ops)
        arr = (SS_Operation * max(n, 1))()
        for i, o in enumerate(ops):
            otype = o.get("type", SS_OP_READ)
            if isinstance(otype, str):
                otype = _OP_TYPES.get(otype.upper(), SS_OP_READ)
            content = o.get("content")
            if content is not None:
                if isinstance(content, str):
                    content = content.encode("utf-8")
                if b"\x00" in content:
                    raise ValueError(
                        f"op[{i}] binary content (NUL bytes) is not supported "
                        "by the text-oriented kernel API")
            arr[i] = SS_Operation(
                type=int(otype),
                path=_b(o.get("path")),
                content=_b(content),
                pattern=_b(o.get("pattern")),
                target=_b(o.get("target")),
                command=_b(o.get("command")),
                line_start=int(o.get("line_start", 0)),
                line_end=int(o.get("line_end", 0)),
                max_results=int(o.get("max_results", 0)),
            )
        targets = None
        tcount = 0
        if target_paths:
            targets = (C.c_char_p * len(target_paths))(*[_b(t) for t in target_paths])
            tcount = len(target_paths)
        plan = SS_Plan(
            ops=C.cast(arr, C.POINTER(SS_Operation)) if n else None,
            op_count=n,
            context_strategy=int(strategy),
            target_paths=C.cast(targets, C.POINTER(C.c_char_p)) if targets else None,
            target_path_count=tcount,
            max_loops=max_loops,
        )
        ptr = lib.ss_execute(self._ptr, C.byref(plan))
        if not ptr:
            raise RuntimeError("ss_execute returned NULL")
        return _parse_result(ptr)

    # -- convenience ops ----------------------------------------------
    def read(self, path, line_start=0, line_end=0,
             strategy=SS_CONTEXT_DELTA) -> str:
        r = self.execute([{
            "type": SS_OP_READ, "path": path,
            "line_start": line_start, "line_end": line_end,
        }], strategy=strategy)
        return r.logs[0] if r.logs else ""

    def write(self, path, content) -> str:
        r = self.execute([{"type": SS_OP_WRITE, "path": path, "content": content}])
        return r.logs[0] if r.logs else ""

    def grep(self, pattern, target=None, max_results=0) -> str:
        r = self.execute([{
            "type": SS_OP_GREP, "pattern": pattern, "target": target,
            "max_results": max_results,
        }])
        return r.logs[0] if r.logs else ""

    def diff(self, path=None) -> str:
        r = self.execute([{"type": SS_OP_DIFF, "path": path}])
        return r.logs[0] if r.logs else ""

    def status(self) -> str:
        r = self.execute([{"type": SS_OP_STATUS}])
        return r.logs[0] if r.logs else ""

    def ast_parse(self, path, mode="summary") -> str:
        """mode: 'summary' (default) or 'sexp' (full tree)."""
        r = self.execute([{"type": SS_OP_AST_PARSE, "path": path, "pattern": mode}])
        return r.logs[0] if r.logs else ""

    def ast_query(self, path, pattern) -> str:
        """Run a tree-sitter query; returns 'path:row:col:capture:type:text' lines."""
        r = self.execute([{"type": SS_OP_AST_QUERY, "path": path, "pattern": pattern}])
        return r.logs[0] if r.logs else ""

    def execute_cmd(self, command) -> str:
        r = self.execute([{"type": SS_OP_EXECUTE, "command": command}])
        return r.logs[0] if r.logs else ""

    # -- lazy file access ---------------------------------------------
    def read_file(self, path) -> str:
        self._check()
        raw = lib.ss_read_file(self._ptr, _b(path))
        if raw is None:
            raise FileNotFoundError(path)
        return _decode(raw)

    def write_file(self, path, content) -> None:
        self._check()
        if isinstance(content, str):
            content = content.encode("utf-8")
        if b"\x00" in content:
            raise ValueError("binary content (NUL bytes) is not supported "
                             "by the text-oriented kernel API")
        if lib.ss_write_file(self._ptr, _b(path), _b(content)) != 0:
            raise OSError(f"ss_write_file failed for {path!r}")

    # -- state persistence / recovery ----------------------------------
    def checkpoint(self) -> int:
        self._check()
        return int(lib.ss_checkpoint(self._ptr))

    def rollback(self, checkpoint_id) -> None:
        self._check()
        if lib.ss_rollback(self._ptr, checkpoint_id) != 0:
            raise ValueError(f"unknown checkpoint {checkpoint_id}")

    def save(self, path) -> None:
        self._check()
        if lib.ss_state_save(self._ptr, _b(path)) != 0:
            raise OSError(f"ss_state_save failed for {path!r}")

    @classmethod
    def load(cls, path) -> "SwarmState":
        ptr = lib.ss_state_load(_b(path))
        if not ptr:
            raise ValueError(f"ss_state_load failed for {path!r}")
        st = cls.__new__(cls)
        st._ptr = ptr
        st._closed = False
        return st

    # -- documentation layer (symbol summaries) ---------------------------
    def symbol_summary(self, path=None, pattern=None, target=None) -> str:
        """SS_OP_SYMBOL_SUMMARY: compact symbol summaries (functions/
        structs/enums with signature, line range, in-file calls/callers).

        path limits to one file (None = whole repo); pattern = name
        substring; target = exact symbol name. Returns the kernel's
        rendered text; requires a tree-sitter build (else escalates)."""
        self._check()
        op = {"type": SS_OP_SYMBOL_SUMMARY}
        if path:
            op["path"] = path
        if pattern:
            op["pattern"] = pattern
        if target:
            op["target"] = target
        r = self.execute([op])
        return "\n".join(r.logs) if r.logs else ""

    def build_symbol_index(self, path_filter=None) -> int:
        """Build/refresh the symbol cache (whole repo or one file).
        Returns the number of symbols indexed (-1 without tree-sitter)."""
        self._check()
        return int(lib.ss_build_symbol_index(self._ptr, _b(path_filter)))

    def invalidate_symbols(self, paths) -> None:
        """Drop cached summaries for repo-relative paths (out-of-band
        edits). Returns None; raises OSError on failure."""
        self._check()
        arr = (C.c_char_p * max(len(paths), 1))(*[_b(p) for p in paths])
        if lib.ss_invalidate_symbols(self._ptr, arr, len(paths)) != 0:
            raise OSError("ss_invalidate_symbols failed")

    # -- machine snapshot (sysinfo) --------------------------------------
    def sysinfo(self) -> dict:
        """Fresh machine snapshot from the kernel: mem (kB), loadavg,
        disk free/total (bytes), battery (pct/charging), uptime (s).
        Refreshed on every call — cheap /proc + statvfs reads."""
        self._check()
        si = SS_SystemInfo()
        if lib.ss_sysinfo(self._ptr, C.byref(si)) != 0:
            raise OSError("ss_sysinfo failed")
        return {
            "mem_total_kb": si.mem_total_kb,
            "mem_avail_kb": si.mem_avail_kb,
            "mem_free_kb": si.mem_free_kb,
            "load1": si.load1, "load5": si.load5, "load15": si.load15,
            "disk_free_bytes": si.disk_free_bytes,
            "disk_total_bytes": si.disk_total_bytes,
            "battery_pct": si.battery_pct,
            "battery_charging": si.battery_charging,
            "uptime_sec": si.uptime_sec,
        }

    def resource_tag(self) -> str:
        """Stable bucketed resource tag, e.g. 'm0:d0:bc'. Folds into the
        state hash, so plan outcomes in the registry are tied to the
        resource conditions they ran under."""
        self._check()
        tag = lib.ss_resource_tag(self._ptr)
        return _decode(tag) or ""

    def journal_hash(self) -> str:
        """Rolling SHA-256 hash (SNAP-6 tamper-evidence) folding every
        registry record and audit ledger event."""
        self._check()
        h = lib.ss_journal_hash(self._ptr)
        return _decode(h) or ""

    # -- performance registry -------------------------------------------
    def record_latency(self, signature, kernel, latency_us) -> None:
        self._check()
        if lib.ss_record_latency(self._ptr, _b(signature), _b(kernel), latency_us) != 0:
            raise OSError("ss_record_latency failed")

    def best_kernel(self, signature) -> float:
        self._check()
        return float(lib.ss_get_best_kernel(self._ptr, _b(signature)))

    def best_kernel_name(self, signature):
        self._check()
        name = lib.ss_best_kernel_name(self._ptr, _b(signature))
        return _decode(name)

    def success_rate(self, signature) -> float:
        """Mechanical success rate [0,1] for a signature (0.0 if unseen)."""
        self._check()
        return float(lib.ss_get_success_rate(self._ptr, _b(signature)))

    def feedback(self, signature, ok: bool, kernel: str | None = None) -> None:
        """Record a plan-level success/failure. Semantic layer: benchmarks
        and user feedback feed this; the kernel records it against the
        signature so the registry correlates strategy with correctness.

        kernel defaults to 'plan@<resource-tag>' so registry entries are
        contextual: the same plan under tight memory gets its own samples,
        and the aggregate success_rate() folds all contexts together."""
        self._check()
        k = kernel if kernel is not None else "plan@" + self.resource_tag()
        if lib.ss_record_result(self._ptr, _b(signature), _b(k), int(bool(ok))) != 0:
            raise OSError("ss_record_result failed")

    def registry_timing(self, signature: str) -> dict:
        """Temporal registry view for one signature, aggregated across
        kernels: samples, fb_samples, avg_us, min_us, max_us (latency)
        and the monotonic last_ts_ms of the most recent record (latency
        or feedback). fb_samples counts semantic feedback records, so a
        strategy key can distinguish "unseen" (0) from "all failed"
        (rate 0.0 with samples > 0). All zeros when the signature is
        unseen. Lets the orchestrator factor time budget into
        strategy/confidence."""
        self._check()
        out = SS_RegistryStats()
        if lib.ss_registry_timing(self._ptr, _b(signature), C.byref(out)) != 0:
            raise OSError("ss_registry_timing failed")
        return {
            "samples": out.samples,
            "fb_samples": out.fb_samples,
            "avg_us": out.avg_us,
            "min_us": out.min_us,
            "max_us": out.max_us,
            "last_ts_ms": out.last_ts_ms,
        }

    # -- state hash ------------------------------------------------------
    @property
    def state_hash(self) -> str:
        return self.execute([]).state_hash


__all__ = [
    "SwarmState", "PlanResult", "lib",
    "SS_OP_READ", "SS_OP_WRITE", "SS_OP_GREP", "SS_OP_DIFF", "SS_OP_STATUS",
    "SS_OP_EXECUTE", "SS_OP_AST_PARSE", "SS_OP_AST_QUERY",
    "SS_CONTEXT_DELTA", "SS_CONTEXT_TARGETED", "SS_CONTEXT_FULL",
    "SS_CONTEXT_SYMBOLIC",
]
