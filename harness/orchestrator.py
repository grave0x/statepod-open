"""Orchestrator: the agent loop.

    query -> planner -> kernel (batch) -> state update -> reply

The LLM (or mock rules) only ever sees summaries produced by the
kernel; the kernel holds and executes the context locally.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

from statepod import (  # noqa: E402
    StatePod,
    SP_CONTEXT_DELTA, SP_CONTEXT_TARGETED, SP_CONTEXT_FULL,
    SP_CONTEXT_SYMBOLIC,
)
from planner import (  # noqa: E402
    make_plan, decide_strategy, normalize_plan, validate_plan,
    plan_signature, op_signature, last_usage as _planner_usage,
)
from router import pick_model, pick_model_rates, query_sig  # noqa: E402

STRATEGIES = {"DELTA": SP_CONTEXT_DELTA, "TARGETED": SP_CONTEXT_TARGETED,
              "FULL": SP_CONTEXT_FULL, "SYMBOLIC": SP_CONTEXT_SYMBOLIC}


def _exec(state, ops, strategy_name, targets, max_loops=4):
    # max_loops: kernel-side stuck-loop guard. The same failing plan must
    # repeat 4x consecutively to trip it — a genuine runaway, not a normal
    # retry (retry/escalation produce different plans).
    return state.execute(
        ops,
        strategy=STRATEGIES[strategy_name],
        target_paths=targets,
        max_loops=max_loops,
    )


def plan_confidence(state, ops) -> float:
    """0..1 registry-driven confidence for a plan. Uses the mechanical
    success rate of the op signatures when they have history; falls back
    to a heuristic baseline for unseen signatures (cold start)."""
    sigs = {op_signature(o) for o in ops}
    rates = [state.success_rate(s) for s in sigs]
    known = [r for r in rates if r > 0.0]
    if known:
        return sum(known) / len(known)
    return 0.7  # heuristic baseline: schema already passed the gate


def _retry_hint(reason: str | None) -> str:
    """Targeted guidance appended to the re-plan prompt when a failure
    has a known, model-fixable cause. Local models repeat bad tree-sitter
    patterns even after seeing 'invalid node type' — the structural fix
    is to forbid the failing op shape on retry and point at the doc
    layer (SYMBOL_SUMMARY carries used_by/calls edges, which is what
    'find unused' needs)."""
    if not reason:
        return ""
    if "query invalid" in reason or "invalid node type" in reason:
        return (" Do NOT use AST_QUERY on the retry (the previous pattern "
                "was invalid). Use SYMBOL_SUMMARY (symbol summaries with "
                "signatures and used_by/calls edges) or READ instead.")
    if "pattern is required" in reason or "path is required" in reason:
        return (" Do NOT use AST_QUERY on the retry (its pattern/path "
                "fields are REQUIRED and were omitted). Use "
                "SYMBOL_SUMMARY with path/target to list or describe "
                "symbols instead.")
    if "cannot read file" in reason:
        return (" Do NOT use AST_QUERY on the retry (the kernel could "
                "not read the query path — it must be an EXISTING repo "
                "file, relative to the root). Use SYMBOL_SUMMARY with "
                "the existing file path instead.")
    return ""


class Orchestrator:
    def __init__(self, root: str, backend: str = "mock",
                 model: str | None = None,
                 models: list[str] | None = None, verbose: bool = False,
                 mesh=None, governance=None, mock_auth_token=None,
                 plan_grammar: str = "lenient",
                 use_registry_strategy: bool = True):
        self.plan_grammar = plan_grammar
        if governance is None:
            from governance import Governance
            governance = Governance(mode="off")
        self.governance = governance
        # mock_auth_token: deterministic demo path -- stamp governed ops
        # of mock plans with a supervisor-issued token (the model itself
        # would include auth for real backends; prompt-injection of
        # short-lived tokens is future work).
        self.mock_auth_token = mock_auth_token
        # use_registry_strategy=False forces the op-shape heuristic
        # (maturity bench A/B baseline). Default True = §9.3 rates.
        self.use_registry_strategy = use_registry_strategy
        self.state = StatePod(root)
        # mesh=None (default) or a MeshDaemon (harness/meshd.py): when
        # set, every feedback() call also publishes the learning as mesh
        # ops so peer nodes fold it into their own registry memory.
        self.mesh = mesh
        self.root = str(root)
        self.backend = backend
        # Auto-start ollama serve if we need a local model
        if backend == "ollama":
            # Default store: statepod/models/ -> ~/.ollama/models
            repo_root = Path(__file__).resolve().parent.parent
            store_path = repo_root / "models"
            try:
                from model_store import ensure_ollama_running
                base_url = ensure_ollama_running(shared_store=store_path)
                # Set the base URL for planner.ollama so it uses the right port
                os.environ.setdefault("STATEPOD_OLLAMA", base_url)
            except Exception as exc:
                print(f"[orchestrator] WARNING: could not auto-start ollama ({exc})")
        self.model = model
        # models= enables per-task routing (harness/router.py): the
        # first entry is the fallback default, and a schema-rejected
        # plan retries once with the next model before giving up.
        self.models = models or ([model] if model else None)
        self.last_model = model
        self.last_query_sig = None
        self.last_write_ctx = 0
        self.verbose = verbose
        self.turns = 0
        self.last_plan_sig = None
        self.last_state_hash = None
        self.last_prompt_bytes = 0
        self.last_usage = {}
        self.last_strategy = None
        self.last_retries = 0
        self._fb_log = Path.home() / ".local/state/statepod/pending_feedback.jsonl"
        self._fb_log.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_write_context(self, query: str, summary: str,
                             plan: dict) -> tuple[dict, str]:
        """Blind-write guard (hard-3 root cause, 2026-08-28).

        The planner sees git status + state hash only (containment), so
        a plan that WRITEs without the model ever seeing the file
        rewrites it blind — the live WEAK cluster (lint remove wrote
        18-64 byte truncations; rename ptr->phead wrote 17 bytes).
        When a validated plan contains WRITE ops, re-plan ONCE with the
        target files' current content appended. Bounded: one extra
        model call per ask() only for write plans; retry rungs reuse
        the enriched summary (the local `summary` variable). Returns
        (plan, summary) — falls back to the original on any failure.
        """
        writes = [o.get("path") for o in plan.get("ops", [])
                  if o.get("type") == "WRITE" and o.get("path")]
        if not writes:
            return plan, summary
        blocks = []
        for path in dict.fromkeys(writes):
            try:
                data = self.state.read(path)
            except Exception:
                continue
            if not data.strip() or data.lstrip().startswith("ERROR"):
                # new file / unreadable path (absolute, outside repo):
                # nothing to show — model writes fresh; error strings
                # are NOT content
                continue
            if len(data) > 12000:
                data = data[:12000] + "\n... (truncated)\n"
            blocks.append(f"=== {path} (current content to rewrite) ==="
                          f"\n{data}")
        if not blocks:
            return plan, summary
        enriched = (summary + "\n" + "\n".join(blocks)
                    + "\nYou are about to WRITE these files. Their "
                      "current content is above — emit the complete new "
                      "file(s) with the requested edit applied, keeping "
                      "everything else identical.")
        try:
            plan2 = normalize_plan(make_plan(
                query, enriched, backend=self.backend,
                model=self.last_model, grammar=self.plan_grammar))
            ok2, _errors2 = validate_plan(plan2)
            if ok2:
                self.last_write_ctx = 1
                if self.verbose:
                    print(f"  [write-context] re-planned with target "
                          f"content ({len(enriched)} B summary)",
                          file=sys.stderr)
                return plan2, enriched
        except RuntimeError:
            pass
        return plan, summary

    def other_model(self) -> str | None:
        """The OTHER configured model (diversity retry): oracle-in-the-
        loop retries prefer a fresh brain because the two local models
        fail differently (7B echoes files back unchanged, 1.5B
        truncates them)."""
        if self.models and len(self.models) > 1:
            for m in self.models:
                if m != self.last_model:
                    return m
        return None

    def feedback(self, ok: bool, signature: str | None = None) -> str:
        """Record semantic success/failure for the last plan (or a given
        signature) into the registry. This closes the learning loop:
        mechanical success is the kernel's truth; this is the user's."""
        sig = signature or self.last_plan_sig
        if not sig:
            return "no plan recorded to feedback on"
        self.state.feedback(sig, ok)
        # strategy-tagged key: lets the registry learn which strategy
        # wins per task signature (adaptive SYMBOLIC switch later)
        if self.last_strategy:
            self.state.feedback(f"STRAT:{sig}:{self.last_strategy}", ok)
            # model-scoped keys: MODEL:<sig>:<model> is the Phase 4.5
            # routing signal (which brain wins per signature); the
            # model-tagged STRAT key keeps strategy learning per model
            if self.last_model:
                self.state.feedback(
                    f"STRAT:{sig}:{self.last_strategy}:{self.last_model}", ok)
                self.state.feedback(f"MODEL:{sig}:{self.last_model}", ok)
                # query-keyed rate (usable at routing time: the plan sig
                # is unknown before the model is picked)
                if self.last_query_sig:
                    self.state.feedback(
                        f"MODEL:{self.last_query_sig}:{self.last_model}", ok)
        # live mesh learning: share the STRAT sample + the query-keyed
        # model rate as mesh ops (the same keys the rate-based router
        # reads).  Peers fold these into their own registry memory.
        if self.mesh is not None:
            if self.last_strategy:
                self.mesh.publish(
                    f"reg/STRAT/{sig}/{self.last_strategy}",
                    "1" if ok else "0")
            if self.last_query_sig and self.last_model:
                self.mesh.publish(
                    f"reg/MODEL/{self.last_query_sig}/{self.last_model}",
                    "1" if ok else "0")
        return f"recorded {'OK' if ok else 'FAIL'} for {sig} " \
            f"(strat={self.last_strategy} model={self.last_model})"

    def pending_feedback(self) -> str:
        """Show recent turns that can still be marked correct/incorrect
        (delayed feedback: e.g. a refactor that only proves itself after
        tests run)."""
        if not self._fb_log.exists():
            return "(no pending feedback)"
        lines = self._fb_log.read_text().splitlines()[-10:]
        out = []
        for ln in lines:
            parts = ln.split("\t")
            # legacy lines: turn/sig/hash/backend ; new: + epoch seconds
            if len(parts) >= 5 and parts[4].isdigit():
                ts = time.strftime("%H:%M:%S", time.localtime(int(parts[4])))
                out.append(f"{ts}  turn={parts[0]} sig={parts[1]} "
                           f"hash={parts[2][:10]} backend={parts[3]}")
            else:
                out.append(ln)
        return "\n".join(out) or "(no pending feedback)"

    # -- the loop -----------------------------------------------------
    def ask(self, query: str, fast: bool = False, escalate: bool = True,
            feedback: str | None = None,
            force_model: str | None = None,
            plan_callback=None) -> str:
        """One query -> plan -> execute. On failure or kernel escalation,
        re-plans once with the error fed back, escalating the backend
        (local -> cloud) and widening the context strategy.

        fast=True is the "quick answer" mode: at most ONE plan attempt,
        no re-plan retry rung and no cloud escalation. Use when latency
        matters more than the second chance (registry timings let you
        decide when).

        escalate=False disables the local->cloud escalation rung only
        (the same-backend re-plan rung still runs). Used for honest
        local-only measurements (e.g. an ollama suite that must not be
        silently rescued by deepseek).

        plan_callback(plan): invoked with the FINAL plan (after the
        blind-write guard re-plan, before governance stamping) so a
        caller can display it and gate execution.  If it raises, the
        exception propagates out of ask() unchanged (nothing executed)."""
        self.turns += 1
        self.last_retries = 0
        backend = self.backend
        model = force_model or pick_model_rates(query, self.models,
                                                self.state)
        self.last_model = model
        self.last_query_sig = query_sig(query)
        summary = (self._full_dump() if backend == "normal"
                   else self._state_summary())
        if feedback:
            summary += ("\nThe previous plan EXECUTED but did NOT achieve "
                        "the task. Oracle: " + feedback +
                        " Re-plan to actually fix it — the oracle re-checks "
                        "the repo state after execution.")
        self.last_prompt_bytes = len(summary)
        self.last_usage = {}
        try:
            plan = normalize_plan(make_plan(
                query, summary, backend=backend, model=model, mesh=self.mesh,
                mesh_prefer=([model] if (backend == "mesh" and model)
                             else None), grammar=self.plan_grammar))
        except (RuntimeError, ValueError) as exc:
            # spec v2 §6.1: "falls back to local inference or another node
            # on timeout" -- a mesh:// consumer degrades to its own model.
            if backend == "mesh" and self.model:
                self.last_retries += 1
                try:
                    plan = normalize_plan(make_plan(
                        query, summary, backend="ollama", model=self.model,
                        grammar=self.plan_grammar))
                except (RuntimeError, ValueError) as exc2:
                    return (f"[turn {self.turns}] mesh planner failed "
                            f"({exc}); local fallback failed ({exc2})")
            else:
                return (f"[turn {self.turns}] planner failed (backend={backend}): {exc}")
        ok, errors = validate_plan(plan)
        if ok and not fast:
            # blind-write guard: a WRITE plan whose model never saw the
            # file produces truncated hallucinated rewrites (the hard-3
            # root cause: lint remove wrote 18-64 bytes). Re-plan once
            # with the target files' actual content appended.
            plan, summary = self._ensure_write_context(query, summary, plan)
        self.last_prompt_bytes = len(summary)
        self.last_usage = dict(_planner_usage)

        # caller gate: display/approve the FINAL plan before governance
        # stamping and execution (raises to abort -> nothing executed).
        if plan_callback is not None:
            plan_callback(plan)

        # stamp governed ops on the FINAL plan (the blind-write guard may
        # have re-planned above, which would otherwise present unstamped
        # ops to the gate -- a false DENY for governed WRITE plans).
        # mock_auth_token: deterministic demo path -- the model itself
        # would include auth for real backends (prompt-injection of
        # short-lived tokens is future work).
        if self.mock_auth_token and backend == "mock":
            for _op in plan.get("ops", []):
                if str(_op.get("type", "")).upper() in                         self.governance.governed_ops:
                    _op.setdefault("auth_token", self.mock_auth_token)
                    _op.setdefault(
                        "reason", "authorized by mock supervisor token")

        # governance gate (spec v1 §6): governed ops need a valid
        # supervisor token AND a recorded reason.  Enforce = deny the
        # whole plan ("no exceptions"); interactive = human override,
        # always logged.  Runs on the FINAL plan (after the blind-write
        # guard may have re-planned).  The mechanical layer (kernel
        # allowlist) is already enforced at execution.
        gv = self.governance.gate(plan["ops"],
                                  plan_sig=plan_signature(plan["ops"]))
        if gv and any(e["verdict"] == "DENY" for e in gv):
            if self.governance.mode == "interactive":
                for _e in gv:
                    if _e["verdict"] == "DENY":
                        self.governance.human_approve(_e)
            self.governance.record_all(gv)
            if any(e["verdict"] in ("DENY", "HUMAN_DENY") for e in gv):
                return (f"[turn {self.turns}] governance: plan denied: "
                        f"{self.governance.describe(gv)}")
        elif gv:
            self.governance.record_all(gv)

        result = None
        strategy = None
        confidence = None
        if ok:
            confidence = plan_confidence(self.state, plan["ops"])
            strategy, targets = decide_strategy(plan["ops"], plan.get("strategy"),
                                                    registry=(self.state if self.use_registry_strategy
                                                               else None))
            if self.verbose:
                print(f"  [plan] {plan} -> {strategy} conf={confidence:.2f}",
                      file=sys.stderr)
            result = _exec(self.state, plan["ops"], strategy, targets)

        # plan-rejection retry rung: a schema-invalid plan (e.g. a local
        # model omitting a required field like AST_QUERY.pattern) used to be
        # a dead end under escalate=False. Feed the validation errors back
        # and re-plan once, exactly like the execution retry rungs below.
        if (not ok) and backend in ("ollama", "deepseek", "normal",
                                  "hw", "harness") and not fast:
            reason = "; ".join(errors or ["schema errors"])
            if self.verbose:
                print(f"  [retry] {backend} re-plan (plan rejected: {reason})",
                      file=sys.stderr)
            self.last_retries += 1
            plan = normalize_plan(make_plan(
                query,
                summary + f"\nThe previous plan was rejected by the schema: "
                          f"{reason}. Fix the plan and return valid JSON."
                          + _retry_hint(reason),
                backend=backend, model=model, grammar=self.plan_grammar))
            ok, errors = validate_plan(plan)
            if ok:
                confidence = plan_confidence(self.state, plan["ops"])
                strategy, targets = decide_strategy(plan["ops"],
                                                        plan.get("strategy"),
                                                        registry=(self.state if self.use_registry_strategy
                                                               else None))
                result = _exec(self.state, plan["ops"], strategy, targets)
            # cross-model rung: the same model failed the schema twice;
            # try the next configured model once before giving up (the
            # two local models fail differently, so a fresh brain often
            # fixes it: 7B echoes files, 1.5B truncates).
            elif self.models and len(self.models) > 1:
                alt = [m for m in self.models if m != model]
                if alt and not fast:
                    alt_model = alt[0]
                    if self.verbose:
                        print(f"  [retry] {backend} cross-model "
                              f"({model} -> {alt_model})", file=sys.stderr)
                    self.last_retries += 1
                    plan = normalize_plan(make_plan(
                        query,
                        summary + f"\nThe previous plan was rejected by "
                                  f"the schema: {reason}. Fix the plan and "
                                  f"return valid JSON."
                                  + _retry_hint(reason),
                        backend=backend, model=alt_model,
                        grammar=self.plan_grammar))
                    self.last_model = alt_model
                    ok, errors = validate_plan(plan)
                    if ok:
                        confidence = plan_confidence(self.state,
                                                     plan["ops"])
                        strategy, targets = decide_strategy(
                            plan["ops"], plan.get("strategy"),
                            registry=(self.state if self.use_registry_strategy
                                                               else None))
                        result = _exec(self.state, plan["ops"],
                                       strategy, targets)
                    else:
                        result = None

        # rephrase-retry rung: a local LLM (ollama) gets one re-plan with
        # the error fed back before we pay for cloud escalation. mock is
        # deterministic, so it escalates straight to deepseek.
        if (result is not None and not result.ok) and backend == "ollama" and not fast:
            reason = result.error_message or result.escalation_reason
            if self.verbose:
                print(f"  [retry] ollama re-plan ({reason})", file=sys.stderr)
            self.last_retries += 1
            plan = normalize_plan(make_plan(query,
                                            summary + f"\nThe previous plan failed: {reason}\nFix it.{_retry_hint(reason)}",
                                            backend="ollama", model=model,
                                            grammar=self.plan_grammar))
            ok, errors = validate_plan(plan)
            if ok:
                confidence = plan_confidence(self.state, plan["ops"])
                strategy, targets = decide_strategy(plan["ops"], plan.get("strategy"),
                                                        registry=(self.state if self.use_registry_strategy
                                                               else None))
                result = _exec(self.state, plan["ops"], strategy, targets)
            else:
                result = None

        # self-correct rung: deepseek/normal are the top of the ladder, so
        # they get ONE re-plan with the error fed back instead of failing
        # outright on a single bad op (e.g. an invalid AST_QUERY pattern or
        # a wrong path the model emitted). Keeps apples-to-apples parity
        # between the contained and naive baselines (both retry the same).
        if (result is not None and not result.ok) \
                and backend in ("deepseek", "normal") and not fast:
            reason = result.error_message or result.escalation_reason
            if self.verbose:
                print(f"  [retry] {backend} re-plan ({reason})", file=sys.stderr)
            self.last_retries += 1
            try:
                plan = normalize_plan(make_plan(
                    query,
                    summary + f"\nThe previous plan failed: {reason}\nFix it.{_retry_hint(reason)}",
                    backend=backend, model=model,
                    grammar=self.plan_grammar))
            except (RuntimeError, ValueError) as exc:
                plan = None
                errors = [f"re-plan failed: {exc}"]
            if plan is not None:
                ok, errors = validate_plan(plan)
                if ok:
                    confidence = plan_confidence(self.state, plan["ops"])
                    strategy, targets = decide_strategy(plan["ops"],
                                                            plan.get("strategy"),
                                                            registry=(self.state if self.use_registry_strategy
                                                               else None))
                    result = _exec(self.state, plan["ops"], strategy, targets)
                else:
                    result = None

        if (result is None or not result.ok or result.needs_escalation) \
                and backend not in ("deepseek", "normal") and not fast \
                and escalate:
            reason = ("; ".join(errors) if not ok else
                      (result.escalation_reason or result.error_message
                       if result else "unknown"))
            if self.verbose:
                _esc_label = "hw" if backend in ("hw", "harness") else "deepseek"
                print(f"  [escalate] {backend} -> {_esc_label} ({reason})",
                      file=sys.stderr)
            listing = "(unavailable)"
            try:
                listing = "\n".join(
                    _exec(self.state,
                          [{"type": "EXECUTE", "command": "find . -type f"}],
                          "DELTA", None).logs[0].splitlines()[:30])
            except Exception:
                pass
            summary2 = (summary
                        + f"\nA previous plan was invalid or failed: {reason}"
                        + "\nFix the plan: check paths, op types, and arguments."
                        + "\nPaths must be relative to the repo root (no absolute"
                        + " paths, no '..')."
                        + f"\nFiles in the repo:\n{listing}")
            try:
                _esc_backend = ("hw" if backend in ("hw", "harness")
                                else "deepseek")
                plan = normalize_plan(make_plan(query, summary2,
                                                backend=_esc_backend,
                                                model=model,
                                                grammar=self.plan_grammar))
            except (RuntimeError, ValueError) as exc:
                return (f"[turn {self.turns}] escalation planner failed: {exc}")
            ok, errors = validate_plan(plan)
            if ok:
                confidence = plan_confidence(self.state, plan["ops"])
                strategy, targets = decide_strategy(plan["ops"], plan.get("strategy"),
                                                        registry=(self.state if self.use_registry_strategy
                                                               else None))
                result = _exec(self.state, plan["ops"], strategy, targets)
            else:
                result = None
            backend = _esc_backend

        self.last_plan_sig = plan_signature(plan["ops"]) if plan else None
        self.last_strategy = strategy
        self.last_state_hash = (result.state_hash if result else
                                self.state.state_hash)
        with self._fb_log.open("a") as f:
            f.write(f"{self.turns}\t{self.last_plan_sig}\t"
                    f"{self.last_state_hash[:16]}\t{backend}\t"
                    f"{int(time.time())}\t{'fast' if fast else 'full'}\n")
        return self._render(result, plan, strategy, backend, errors, confidence)

    def _state_summary(self) -> str:
        """A tiny context summary for the planner: repo status + hashes.
        The kernel produced both; no files are read by the orchestrator."""
        try:
            status = self.state.status()
        except Exception:
            status = "(not a git repo)"
        return f"git status:\n{status}\nstate hash: {self.state.state_hash[:16]}"

    def _full_dump(self, max_bytes: int = 262144) -> str:
        """NAIVE baseline context: the entire repo contents, exactly what
        a normal full-context agent would dump into the prompt. Used only
        by backend='normal' for the token/cost comparison. Bounded: 32 KiB
        per file, 256 KiB total."""
        import os
        root = Path(self.root).resolve()
        parts = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", ".statepod", "__pycache__")]
            for fn in sorted(filenames):
                if fn.endswith((".pyc", ".so", ".o", ".a")):
                    continue
                fp = Path(dirpath) / fn
                rel = fp.relative_to(root)
                try:
                    data = fp.read_text(errors="replace")
                except OSError:
                    continue
                if len(data) > 32768:
                    data = data[:32768] + "\n... (truncated)\n"
                block = f"--- {rel} ---\n{data}\n"
                if total + len(block) > max_bytes:
                    parts.append(f"--- ... remaining files omitted "
                                 f"(dump cap {max_bytes} bytes) ---")
                    break
                parts.append(block)
                total += len(block)
            else:
                continue
            break
        try:
            status = self.state.status()
        except Exception:
            status = "(not a git repo)"
        return ("=== FULL REPOSITORY CONTENT (naive baseline) ===\n"
                + "".join(parts)
                + f"=== END ({total} bytes) ===\n"
                + f"git status:\n{status}\nstate hash: {self.state.state_hash[:16]}")

    def _render(self, result, plan: dict, strategy, backend: str,
               errors: list[str] | None = None, confidence: float | None = None) -> str:
        if result is None:
            return ("[turn %d] plan rejected (backend=%s)\n  schema errors: %s"
                    % (self.turns, backend, "; ".join(errors or ["unknown"])))
        conf = f" conf={confidence:.2f}" if confidence is not None else ""
        lines = [f"[turn {self.turns}] strategy={strategy} backend={backend}{conf} "
                 f"ops={[o['type'] for o in plan['ops']]}"]
        for log in result.logs:
            for chunk in str(log).splitlines()[:12]:
                lines.append("  " + chunk)
        if result.touched_files:
            lines.append(f"touched: {', '.join(result.touched_files)}")
        if result.needs_escalation:
            lines.append(f"!! escalation needed: {result.escalation_reason}")
        elif not result.ok:
            lines.append(f"!! op {result.exit_code} failed: {result.error_message}")
        lines.append(f"state hash: {result.state_hash[:16]}")
        return "\n".join(lines)

    def close(self):
        self.state.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
