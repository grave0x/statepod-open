"""GPU / local-inference info helpers (optional, no hard deps).

`detect_gpu()` parses nvidia-smi when present and returns a small dict
(empty when no NVIDIA GPU / driver). `ollama_processor()` parses
`ollama ps` to report the CPU/GPU split for the running model — the
number that tells you whether the local backend actually benefits from
the GPU (a 7B model on a 2 GB card shows ~4% GPU; a 1.5B model fits and
shows ~100%).
"""
import subprocess
import time

_CACHE: dict = {}
_CACHE_TTL = 30.0


def detect_gpu() -> dict:
    """Best-effort GPU detection: {'name','vram_mb','driver'} or {}."""
    now = time.monotonic()
    if _CACHE.get("gpu_ts", 0) and now - _CACHE["gpu_ts"] < _CACHE_TTL:
        return _CACHE.get("gpu", {})
    info = {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        if out:
            name, vram, drv = [p.strip() for p in out.split(",")[:3]]
            info = {"name": name, "vram_mb": int(float(vram)),
                    "driver": drv}
    except Exception:
        pass
    _CACHE["gpu"] = info
    _CACHE["gpu_ts"] = now
    return info


def ollama_processor() -> str:
    """'96%/4% CPU/GPU' style string from `ollama ps`, or ''."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines()[1:]:
            parts = line.split()
            for tok in parts:
                if "%" in tok and "CPU" in tok:
                    return tok
            # fallback: token right after the two-token SIZE column
            if len(parts) >= 5 and parts[2] == "GB":
                return parts[4]
    except Exception:
        pass
    return ""


def gpu_label() -> str:
    g = detect_gpu()
    if not g:
        return "no-gpu"
    return f"{g['name']}({g['vram_mb']}MB)"


def pick_ngl(model_bytes_mb: int, n_layers: int = 28,
             ctx_kv_mb: int = 224, compute_mb: int = 200,
             reserve_mb: int = 500) -> int:
    """VRAM-aware default layer offload (Phase 4.5 seed).

    How many llama.cpp layers fit the free VRAM after the KV cache,
    compute buffers, and a driver/desktop reserve? Returns 0 when even
    one layer doesn't fit (CPU-only — exactly what ollama auto-chooses
    for a 4.7 GB 7B on a 2 GB card). The registry then learns the rest:
    records carry `ngl` + `processor` + `gpu`, so STRAT: feedback can
    correlate offload setting with task duration/tokens per resource
    state. Heuristic, not gospel: real measurement wins.
    """
    g = detect_gpu()
    if not g:
        return 0
    free = g.get("vram_mb", 0) - reserve_mb
    usable = free - ctx_kv_mb - compute_mb
    if usable <= 0 or n_layers <= 0:
        return 0
    layer_mb = model_bytes_mb / n_layers
    if layer_mb <= 0:
        return 0
    return max(0, min(n_layers, int(usable / layer_mb)))
