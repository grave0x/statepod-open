"""Local model routing (v1: query-shape heuristic + registry learning keys).

The 42-task live corpus (2026-08-28) showed the two local models fail
differently:

  qwen2.5-coder:1.5b  ~4x faster, 32/42 semantic (vs 7b's 30/42),
                      strong on DELTA/TARGETED; WEAK mode is destructive
                      truncation; cannot follow the SYMBOL_SUMMARY
                      instruction for list tasks (schema FAILs).
  qwen2.5-coder:7b    uniquely handles SYMBOLIC and AST_QUERY-heavy
                      plans (list/explain); WEAK mode is echoing files
                      back unchanged (FULL 50%).

v1 routes by query shape. Every semantic verdict ALSO lands in
MODEL:<plan-sig>:<model> and STRAT:<plan-sig>:<strategy>:<model>
registry keys (replayed across runs), so Phase 4.5 can switch to
proven-rate routing without changing the feedback path.
"""
from __future__ import annotations

# 7B keywords: tasks where the 7B demonstrated unique capability.
SEVEN_B_KEYWORDS = ("list", "explain", "symbol", "pattern", "tree",
                    "ast", "query", "enumerate", "signature", "struct",
                    "function_definition", "call_expression")


def pick_model(query: str, models: list[str] | None) -> str | None:
    """Choose the model for a task query.

    Single configured model -> itself. Otherwise: queries mentioning a
    7B-strength keyword go to a '7b'-tagged model, everything else to
    the first non-7b model. Deterministic and testable; the registry
    keys above are what let a later version replace this heuristic
    with proven per-signature rates.
    """
    if not models:
        return None
    if len(models) == 1:
        return models[0]
    q = query.lower()
    prefers_7b = any(k in q for k in SEVEN_B_KEYWORDS)
    for m in models:
        is_7b = "7b" in m.lower()
        if prefers_7b and is_7b:
            return m
        if not prefers_7b and not is_7b:
            return m
    return models[0]


def query_sig(query: str) -> str:
    """Stable routing key for a task query. Model choice happens BEFORE
    the plan exists, so plan-signature keys can't drive it; the query
    is known. Corpus tasks have fixed queries, so their qsig is stable
    across runs and the registry can learn per-task model rates."""
    import hashlib
    return "q:" + hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]


def pick_model_rates(query: str, models: list[str] | None, state=None,
                     min_samples: int = 3) -> str | None:
    """Rate-based model choice: for the query's signature, pick the
    configured model with the best proven semantic rate (MODEL:q:<qsig>
    keys, >= min_samples feedback samples). Ties prefer the first
    non-7b model (fast). Falls back to the query heuristic when the
    registry has no proof yet (cold start / pre-guard era)."""
    if not models:
        return None
    if len(models) == 1:
        return models[0]
    qsig = query_sig(query)
    best, best_rate = None, -1.0
    for m in models:
        key = f"MODEL:{qsig}:{m}"
        if state is not None:
            try:
                fb = state.registry_timing(key).get("fb_samples", 0)
                if fb >= min_samples:
                    rate = state.success_rate(key)
                    if rate > best_rate:
                        best, best_rate = m, rate
            except Exception:
                pass
    if best is not None:
        return best
    return pick_model(query, models)
