"""End-to-end orchestrator loop test (mock backend)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

from unittest import mock  # noqa: E402

from orchestrator import Orchestrator  # noqa: E402
from planner import (  # noqa: E402
    decide_strategy, registry_strategy, normalize_plan, validate_plan,
    _extract_json, _validate_plan, plan_ollama, make_plan,
    plan_freetoken, plan_llamacpp, plan_openai_backend,
    OPENAI_BACKEND_NAMES, OLLAMA_MAX_TOKENS,
)
from swarmstate import SwarmState  # noqa: E402
from task_suite import _replay_feedback, _parse_turn, build_repo, _semantic_ok  # noqa: E402
from corpus import ORACLES, ALL_TASKS  # noqa: E402


class LoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="swarmstate-loop-")
        self.root = self._tmp.name
        subprocess.run(["git", "init", "-q", self.root], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ten_queries_no_crash(self):
        with Orchestrator(self.root, backend="mock") as orch:
            replies = []
            replies.append(orch.ask('write "hello.txt" "hello swarmstate\n"'))
            replies.append(orch.ask('write "src/main.c" "int main(void) { return 0; }\n"'))
            replies.append(orch.ask("read hello.txt"))
            replies.append(orch.ask("grep 'swarmstate'"))
            replies.append(orch.ask("status"))
            replies.append(orch.ask("count lines of hello.txt"))
            replies.append(orch.ask("diff src/main.c"))
            replies.append(orch.ask("read hello.txt"))
            replies.append(orch.ask("grep 'main'"))
            replies.append(orch.ask("status"))
            for r in replies:
                self.assertTrue(r)
            self.assertEqual(orch.turns, 10)
            # state persisted through the kernel cache
            self.assertEqual(orch.state.read_file("hello.txt"), "hello swarmstate\n")
            self.assertEqual(orch.state.read_file("src/main.c"),
                             "int main(void) { return 0; }\n")
            # a write plan touched the right files
            self.assertIn("hello.txt", orch.state.status())

    def test_failed_plan_escalates_backend(self):
        """A plan that fails on the local backend is re-planned once via
        the cloud backend, with the error fed back. Fully offline: the
        cloud call is mocked."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.side_effect = [
                # local (mock) planner produces an invalid plan: absolute path
                {"ops": [{"type": "WRITE", "path": "/tmp/forbidden/x", "content": "x"}],
                 "strategy": "DELTA"},
                # cloud planner fixes it
                {"ops": [{"type": "WRITE", "path": "ok.txt", "content": "fixed\n"}],
                 "strategy": "DELTA"},
            ]
            with Orchestrator(self.root, backend="mock") as orch:
                out = orch.ask("write the fix")
                self.assertEqual(orch.state.read_file("ok.txt"), "fixed\n")
        self.assertIn("backend=deepseek", out)
        self.assertEqual(mp.call_count, 2)

    def test_no_escalate_keeps_backend_local(self):
        """escalate=False disables local->cloud escalation: a failing mock
        plan must NOT be silently rescued by a deepseek call (honest
        local-only measurement)."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.return_value = {"ops": [{"type": "EXECUTE", "command": "rm -rf ."}],
                               "strategy": "DELTA"}
            with Orchestrator(self.root, backend="mock") as orch:
                out = orch.ask("status", escalate=False)
        self.assertIn("!!", out)
        self.assertEqual(mp.call_count, 1)   # no escalation re-plan

    def test_plan_ollama_sends_plain_guarded_request(self):
        """plan_ollama must NOT send response_format (json_object is a
        risk multiplier on local models) and must cap generation with
        max_tokens — verified against a fake OpenAI-compat endpoint."""
        calls = []

        def handler(body):
            calls.append(body)
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        try:
            plan = plan_ollama("status", "git status: clean", base=base)
        finally:
            stop()
        self.assertEqual(plan["ops"][0]["type"], "STATUS")
        self.assertEqual(len(calls), 1, "plain single request, no retry rung")
        body = calls[0]
        self.assertNotIn("response_format", body,
                         "json_object dropped for local models")
        self.assertEqual(body.get("max_tokens"), OLLAMA_MAX_TOKENS,
                         "generation capped so a runaway cannot hang")

    def test_plan_ollama_empty_choices_raises_clean_runtime_error(self):
        """Regression: ollama occasionally returns an empty choices array
        (generation aborted server-side). plan_ollama must raise a clear
        RuntimeError — which make_plan turns into a 'planner failed' turn
        and the retry rung re-plans — instead of a raw IndexError that
        crashes the whole task/suite."""
        def handler(body):
            return 200, {"choices": [], "usage": {"prompt_tokens": 1}}

        base, stop = self._fake_ollama(handler)
        try:
            with self.assertRaises(RuntimeError) as cm:
                plan_ollama("status", "git status: clean", base=base)
        finally:
            stop()
        self.assertIn("no choices", str(cm.exception))
        # and the orchestrator path: a 'planner failed' turn, not a crash
        with mock.patch("planner.plan_ollama",
                        side_effect=RuntimeError("ollama returned no choices")) as po:
            with Orchestrator(self.root, backend="ollama") as orch:
                out = orch.ask("status", escalate=False)
        self.assertIn("planner failed", out)

    def test_plan_rejection_retries_with_schema_errors(self):
        """A schema-invalid plan (e.g. AST_QUERY missing its REQUIRED
        pattern — what qwen emits for 'list the functions in src/math.c')
        used to dead-end under escalate=False. Now the orchestrator feeds
        the schema errors back and re-plans once, like the execution
        retry rungs. Fully offline: make_plan is patched."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.side_effect = [
                # first plan: bare AST_QUERY, no pattern (the real bug)
                {"ops": [{"type": "AST_QUERY", "path": "src/math.c"}],
                 "strategy": "DELTA"},
                # retried plan: valid
                {"ops": [{"type": "STATUS"}], "strategy": "DELTA"},
            ]
            with Orchestrator(self.root, backend="ollama", verbose=True) as orch:
                out = orch.ask("list the functions in src/math.c",
                               escalate=False)
                self.assertEqual(orch.last_retries, 1,
                                 "plan rejection must trigger one re-plan")
        self.assertEqual(mp.call_count, 2)
        self.assertIn("strategy=DELTA", out,
                      "the retried plan executed and rendered normally")
        self.assertNotIn("plan rejected", out)
        self.assertNotIn("!!", out)

    def test_plan_rejection_twice_still_renders_rejected(self):
        """If the re-plan is also schema-invalid, render the rejection
        (no crash, no infinite loop)."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.return_value = {"ops": [{"type": "AST_QUERY", "path": "src/math.c"}],
                               "strategy": "DELTA"}
            with Orchestrator(self.root, backend="ollama") as orch:
                out = orch.ask("list the functions in src/math.c",
                               escalate=False)
        self.assertEqual(mp.call_count, 2, "exactly one re-plan attempt")
        self.assertIn("plan rejected", out)
        self.assertIn("pattern is required", out)

    def test_parse_turn_never_raises_and_reports_reason(self):
        """The suite parser must survive unusual orchestrator output
        instead of dying with a raw IndexError ('list index out of
        range') that hides the real failure."""
        # normal turn line
        s, o, f, reason = _parse_turn(
            "[turn 1] strategy=DELTA backend=ollama conf=0.50 "
            "ops=['STATUS']\n  git status: clean\nstate hash: abc\n")
        self.assertEqual((s, o, f, reason), ("DELTA", "['STATUS']", False, ""))
        # rejected plan: no ops= section -> the old code crashed here
        s, o, f, reason = _parse_turn(
            "[turn 1] plan rejected (backend=ollama)\n"
            "  schema errors: op[0].pattern is required for AST_QUERY\n")
        self.assertEqual(s, "")
        self.assertEqual(o, "")
        self.assertTrue(f)
        self.assertIn("pattern is required", reason)
        # planner failed
        s, o, f, reason = _parse_turn(
            "[turn 1] planner failed (backend=ollama): ollama returned no choices")
        self.assertTrue(f)
        self.assertIn("planner failed", reason)
        # execution failure
        s, o, f, reason = _parse_turn(
            "[turn 1] strategy=DELTA backend=ollama ops=['EXECUTE']\n"
            "  !! op 1 failed: boom\n")
        self.assertTrue(f)
        self.assertIn("boom", reason)
        # total garbage: no [turn line at all
        s, o, f, reason = _parse_turn("(empty)\n")
        self.assertEqual((s, o, f, reason), ("", "", False, ""))

    def test_mock_semantic_tasks_produce_intended_outcome(self):
        """The mock planner must not misroute the 3 semantic tasks: used to
        hit the count/AST_PARSE/grep branches and be WEAK in every suite
        run ('rename ... count' -> wc -l; 'add a function' -> parse; 'find
        unused variables' -> grep), feeding False into the §9.3 registry.
        Now each performs the edit as a full-file WRITE against the
        build_repo() fixture and the semantic oracle passes."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="swarmstate-mock-sem-") as tmp:
            from pathlib import Path
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(str(root), backend="mock") as orch:
                orch.ask('rename "x" to "count" in src/math.c', escalate=False)
            code = (root / "src" / "math.c").read_text()
            self.assertIn("int count = 3", code)
            self.assertNotIn("int x = 3", code)
        with tempfile.TemporaryDirectory(prefix="swarmstate-mock-sem-") as tmp:
            from pathlib import Path
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(str(root), backend="mock") as orch:
                orch.ask('add a function "double avg(double a, double b)" '
                         'to src/math.c', escalate=False)
            code = (root / "src" / "math.c").read_text()
            self.assertIn("double avg(double a, double b)", code)
        with tempfile.TemporaryDirectory(prefix="swarmstate-mock-sem-") as tmp:
            from pathlib import Path
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(str(root), backend="mock") as orch:
                orch.ask("find unused variables in src/math.c", escalate=False)
            code = (root / "src" / "math.c").read_text()
            self.assertNotIn("unused_helper", code)

    def test_plan_ollama_num_gpu_passthrough(self):
        """num_gpu_layers / SS_OLLAMA_NUM_CTX must flow through to the
        NATIVE /api/chat endpoint as options (the /v1 compat endpoint
        ignores options — verified live). No options -> /v1 unchanged."""
        calls = []

        def handler(path, body):
            calls.append((path, body))
            return 200, {"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}

        base, stop = self._fake_ollama(handler)
        try:
            plan_ollama("status", "s", base=base, num_gpu_layers=7)
            path, body = calls[0]
            self.assertTrue(path.endswith("/api/chat"),
                             f"options go to native endpoint, got {path}")
            self.assertEqual(body["options"], {"num_gpu": 7},
                             "explicit ngl overrides")
            with mock.patch.dict("os.environ", {"SS_OLLAMA_NGL": "10"}):
                plan_ollama("status", "s", base=base)
            self.assertEqual(calls[1][1]["options"], {"num_gpu": 10},
                             "env fallback")
            plan_ollama("status", "s", base=base, num_ctx=1024)
            self.assertEqual(calls[2][1]["options"], {"num_ctx": 1024},
                             "num_ctx passthrough")
            with mock.patch.dict("os.environ",
                                 {"SS_OLLAMA_NUM_CTX": "1024",
                                  "SS_OLLAMA_NGL": "8"}):
                plan_ollama("status", "s", base=base)
            self.assertEqual(calls[3][1]["options"],
                             {"num_gpu": 8, "num_ctx": 1024},
                             "both knobs merge into one options dict")
        finally:
            stop()

    def test_plan_ollama_native_unwraps_message_shape(self):
        """Native /api/chat returns {"message":{"content":...}} not
        {"choices":[...]}; the planner must unwrap it."""
        def handler(path, body):
            return 200, {"message": {"content": "not json, but extracted"}}
        base, stop = self._fake_ollama(handler)
        try:
            with self.assertRaises(Exception):
                plan_ollama("status", "s", base=base, num_gpu_layers=5)
        finally:
            stop()

    def test_plan_ollama_default_stays_on_v1(self):
        """Without options, requests keep using /v1/chat/completions and
        the OpenAI-compat response shape."""
        calls = []

        def handler(body):
            calls.append(body)
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        try:
            plan = plan_ollama("status", "s", base=base)
            self.assertEqual(plan["ops"], [{"type": "STATUS"}])
            self.assertNotIn("options", calls[0],
                             "default: ollama auto-configures (0 layers)")
        finally:
            stop()

    def test_plan_freetoken_and_llamacpp_openai_path(self):
        """FreeToken and llama-server share the OpenAI-compat helper;
        make_plan dispatches both and surfaces clean errors when down."""
        calls = []

        def handler(body):
            calls.append(body)
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        try:
            with mock.patch.dict(os.environ, {
                "SWARMSTATE_FREETOKEN_BASE": base,
                "SWARMSTATE_LLAMACPP_BASE": base,
            }):
                p1 = plan_freetoken("status", "clean", model="ft-model")
                p2 = plan_llamacpp("status", "clean", model="gguf")
                self.assertEqual(p1["ops"][0]["type"], "STATUS")
                self.assertEqual(p2["ops"][0]["type"], "STATUS")
                self.assertGreaterEqual(len(calls), 2)
                # no Authorization when local key is empty
                plan = make_plan("status", "clean", backend="freetoken",
                                 model="ft-model")
                self.assertEqual(plan["ops"][0]["type"], "STATUS")
                plan = make_plan("status", "clean", backend="llamacpp",
                                 model="gguf")
                self.assertEqual(plan["ops"][0]["type"], "STATUS")
                plan = make_plan("status", "clean", backend="llama",
                                 model="gguf")
                self.assertEqual(plan["ops"][0]["type"], "STATUS")
        finally:
            stop()

        with mock.patch("planner.plan_freetoken",
                        side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError) as cm:
                make_plan("status", "clean", backend="freetoken",
                          model="x")
            self.assertIn("freetoken planner failed", str(cm.exception))
            self.assertIn("ft serve", str(cm.exception))

        with mock.patch("planner.plan_llamacpp",
                        side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError) as cm:
                make_plan("status", "clean", backend="llamacpp")
            self.assertIn("llamacpp planner failed", str(cm.exception))
            self.assertIn("llama-server", str(cm.exception))

        with self.assertRaises(RuntimeError) as cm:
            plan_freetoken("status", "clean", model=None)
        self.assertIn("--model", str(cm.exception))

    def test_openai_preset_backends(self):
        """Named OpenAI-compat presets (vllm/openai/openrouter/…) dispatch
        through the shared helper; cloud presets require a key."""
        self.assertIn("vllm", OPENAI_BACKEND_NAMES)
        self.assertIn("openai", OPENAI_BACKEND_NAMES)
        self.assertIn("openrouter", OPENAI_BACKEND_NAMES)
        self.assertIn("lmstudio", OPENAI_BACKEND_NAMES)

        calls = []

        def handler(body):
            calls.append(body)
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        try:
            with mock.patch.dict(os.environ, {
                "SWARMSTATE_VLLM_BASE": base,
                "SWARMSTATE_OPENAI_BASE": base,
                "OPENAI_API_KEY": "sk-test",
                "SWARMSTATE_OPENROUTER_BASE": base,
                "OPENROUTER_API_KEY": "or-test",
                "SWARMSTATE_LMSTUDIO_BASE": base,
            }, clear=False):
                for name in ("vllm", "openai", "openrouter", "lmstudio"):
                    plan = make_plan("status", "clean", backend=name,
                                     model="m")
                    self.assertEqual(plan["ops"][0]["type"], "STATUS", name)
                # openai without key fails cleanly
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "",
                                                  "SWARMSTATE_OPENAI_KEY": ""}):
                    with self.assertRaises(RuntimeError) as cm:
                        plan_openai_backend("openai", "q", "s", model="m")
                    self.assertIn("API key", str(cm.exception))
        finally:
            stop()
        self.assertGreaterEqual(len(calls), 4)

    def test_router_rate_based_picks_best_proven_model(self):
        """With >= min_samples feedback per model on the query key, the
        router picks the best-proven brain; below the threshold it falls
        back to the heuristic; single-model config is untouched."""
        import tempfile
        from task_suite import build_repo
        from router import pick_model_rates, query_sig
        models = ["qwen2.5-coder:1.5b", "qwen2.5-coder:7b"]
        with tempfile.TemporaryDirectory(prefix="ss-rateroute-") as tmp:
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(str(root), backend="mock") as orch:
                state = orch.state
                q = "count the lines in src/math.c"
                qsig = query_sig(q)
                # 1.5B: 3/4 ok; 7B: 0/3 — rates must win over heuristic
                # (heuristic would pick 1.5B anyway here, so use a 7B-
                # flavored query to prove the rate overrides it)
                q2 = "list the functions in src/util.c"
                qsig2 = query_sig(q2)
                for _ in range(3):
                    state.feedback(f"MODEL:{qsig2}:qwen2.5-coder:1.5b",
                                   True)
                for _ in range(3):
                    state.feedback(f"MODEL:{qsig2}:qwen2.5-coder:7b",
                                   False)
                # rate says 1.5B even though the query says "list" (7B)
                self.assertEqual(pick_model_rates(q2, models, state),
                                 "qwen2.5-coder:1.5b",
                                 "proven rate overrides the heuristic")
                # below threshold -> heuristic (list -> 7B)
                self.assertEqual(pick_model_rates(q2, models, None),
                                 "qwen2.5-coder:7b",
                                 "cold start falls back to heuristic")
                self.assertEqual(pick_model_rates(q, models, None),
                                 "qwen2.5-coder:1.5b")
                self.assertEqual(pick_model_rates(q, ["qwen2.5-coder:7b"],
                                                  state),
                                 "qwen2.5-coder:7b",
                                 "single model is untouched")
                self.assertIsNone(pick_model_rates(q, None, state))

    def test_replay_prefers_most_recent_records(self):
        """Replay must seed the registry from the LATEST records (the
        blind-write guard changed the capability profile; stale samples
        would poison the router)."""
        import tempfile
        from task_suite import _replay_feedback, build_repo
        with tempfile.TemporaryDirectory(prefix="ss-fresh-") as tmp:
            root = Path(tmp)
            build_repo(root)
            results = Path(tmp) / "suite.jsonl"
            lines = []
            for i in range(5):
                rec = {"backend": "mock", "plan_sig": f"sig{i}",
                       "strategy": "DELTA", "ok": True,
                       "ok_semantic": True, "model": "m",
                       "q_sig": f"q{i}"}
                lines.append(json.dumps(rec))
            results.write_text("\n".join(lines) + "\n")
            with Orchestrator(str(root), backend="mock") as orch:
                n = _replay_feedback(orch.state, results, "mock", limit=3)
                self.assertEqual(n, 3)
                # the LAST 3 (fresh), not the first 3
                self.assertGreater(
                    orch.state.success_rate("STRAT:sig4:DELTA"), 0.0)
                self.assertEqual(
                    orch.state.success_rate("STRAT:sig0:DELTA"), 0.0)
                # query-keyed model rate seeded from the fresh window
                self.assertGreater(
                    orch.state.success_rate("MODEL:q4:m"), 0.0)

    def test_router_pick_model_query_heuristics(self):
        """7B-strength keywords (list/explain/symbol/ast) go to the 7b;
        routine ops go to the small model; single model is untouched."""
        from router import pick_model
        models = ["qwen2.5-coder:1.5b", "qwen2.5-coder:7b"]
        self.assertEqual(pick_model("list the functions in src/util.c",
                                    models), "qwen2.5-coder:7b")
        self.assertEqual(pick_model("explain what src/parse.c does",
                                    models), "qwen2.5-coder:7b")
        self.assertEqual(pick_model("find TODO comments", models),
                         "qwen2.5-coder:1.5b")
        self.assertEqual(pick_model("count the lines in src/math.c",
                                    models), "qwen2.5-coder:1.5b")
        self.assertEqual(pick_model("write a note to notes.md", models),
                         "qwen2.5-coder:1.5b")
        # reversed order still respects the heuristic
        self.assertEqual(pick_model("list functions", models[::-1]),
                         "qwen2.5-coder:7b")
        # single model -> itself regardless of query
        self.assertEqual(pick_model("list functions", ["qwen2.5-coder:1.5b"]),
                         "qwen2.5-coder:1.5b")
        self.assertIsNone(pick_model("anything", None))

    def test_feedback_records_model_scoped_registry_keys(self):
        """After a run with a model, the registry must hold
        MODEL:<sig>:<model> and STRAT:<sig>:<strat>:<model> keys so the
        Phase 4.5 router can learn per-model rates from replayed
        samples."""
        import tempfile
        from task_suite import build_repo
        with tempfile.TemporaryDirectory(prefix="ss-fbkeys-") as tmp:
            root = Path(tmp)
            build_repo(root)
            with Orchestrator(str(root), backend="mock",
                              model="qwen2.5-coder:1.5b") as orch:
                out = orch.ask("list the functions in src/util.c")
                self.assertIn("[turn 1]", out)
                sig = orch.last_plan_sig
                strat = orch.last_strategy
                self.assertIsNotNone(sig)
                orch.feedback(True)
                self.assertGreater(
                    orch.state.success_rate(f"MODEL:{sig}:qwen2.5-coder:1.5b"),
                    0.0, "MODEL:<sig>:<model> key recorded")
                self.assertGreater(
                    orch.state.success_rate(
                        f"STRAT:{sig}:{strat}:qwen2.5-coder:1.5b"),
                    0.0, "model-tagged STRAT key recorded")

    def test_cross_model_fallback_on_schema_rejection(self):
        """With models=[A,B], a schema-rejected plan from A must retry
        with B and record B as the last model (the two local models fail
        differently, so the fresh brain often fixes it)."""
        import tempfile
        from task_suite import build_repo
        calls = []

        def handler(path, body):
            calls.append(body.get("model"))
            if body.get("model", "").endswith(":7b"):
                # 7B keeps omitting the required pattern (its known
                # failure mode on list tasks)
                return 200, {"message": {"content":
                    '{"ops":[{"type":"AST_QUERY","path":"src/util.c"}],'
                    '"strategy":"TARGETED"}'}}
            # 1.5B follows the rule on the retry
            return 200, {"message": {"content":
                '{"ops":[{"type":"SYMBOL_SUMMARY","path":"src/util.c"}],'
                '"strategy":"SYMBOLIC"}'}}

        base, stop = self._fake_ollama(handler)
        try:
            with mock.patch.dict("os.environ",
                                 {"SWARMSTATE_OLLAMA": base}):
                with tempfile.TemporaryDirectory(
                        prefix="ss-xmodel-") as tmp:
                    root = Path(tmp)
                    build_repo(root)
                    with Orchestrator(
                            str(root), backend="ollama",
                            models=["qwen2.5-coder:7b",
                                    "qwen2.5-coder:1.5b"]) as orch:
                        out = orch.ask("list the functions in src/util.c",
                                       escalate=False)
                        self.assertIn("SYMBOL_SUMMARY", out,
                                      "fallback model produced a valid plan")
                        self.assertEqual(orch.last_model,
                                         "qwen2.5-coder:1.5b",
                                         "cross-model fallback recorded")
                        self.assertEqual(orch.last_retries, 2,
                                         "same-model + cross-model retry")
                        self.assertEqual(
                            calls, ["qwen2.5-coder:7b",
                                    "qwen2.5-coder:7b",   # same-model retry
                                    "qwen2.5-coder:1.5b"],
                            "7B twice, then the 1.5B")
        finally:
            stop()

    def test_blind_write_guard_replans_with_target_content(self):
        """A WRITE plan must never execute without the model seeing the
        file (hard-3 root cause): the orchestrator re-plans once with
        the target file's content appended, so truncation/echo rewrites
        stop. Bounded: one extra call, only for write plans."""
        import tempfile
        import json as _json
        from task_suite import build_repo
        calls = []

        def handler(path, body):
            calls.append(body)
            msgs = " ".join(m.get("content", "")
                            for m in body.get("messages", []))
            if len(calls) == 1:
                # blind first attempt: truncating WRITE (the known
                # lint-remove failure mode)
                self.assertNotIn("current content to rewrite", msgs)
                plan = {"ops": [{"type": "WRITE", "path": "src/util.c",
                                 "content": "x"}], "strategy": "DELTA"}
            else:
                # enriched re-plan: the file content must be in the prompt
                self.assertIn("=== src/util.c (current content", msgs)
                self.assertIn("int tmp = 0;", msgs)
                plan = {"ops": [{"type": "WRITE", "path": "src/util.c",
                                 "content": "int tmp = 0;\n"
                                            "int mul(int a, int b) { "
                                            "return a * b; }\n"
                                            "// FIXME: leaks\n"}],
                        "strategy": "DELTA"}
            return 200, {"message": {"content": _json.dumps(plan)}}

        base, stop = self._fake_ollama(handler)
        try:
            with mock.patch.dict("os.environ",
                                 {"SWARMSTATE_OLLAMA": base}):
                with tempfile.TemporaryDirectory(
                        prefix="ss-writectx-") as tmp:
                    root = Path(tmp)
                    build_repo(root)
                    with Orchestrator(str(root), backend="ollama",
                                      model="qwen2.5-coder:1.5b") as orch:
                        out = orch.ask("remove the unused variable ptr "
                                       "from src/util.c", escalate=False)
                        self.assertEqual(len(calls), 2,
                                         "exactly one enriched re-plan")
                        code = (root / "src" / "util.c").read_text()
                        self.assertIn("int mul(int a, int b)", code,
                                      "full file survived the rewrite")
                        self.assertNotIn("int *ptr", code,
                                         "the removal actually happened")
        finally:
            stop()

    def test_ask_feedback_appends_oracle_reason(self):
        """ask(feedback=...) must append the oracle reason to the model
        prompt so the re-plan actually sees why the previous execution
        failed the semantic check."""
        import tempfile
        from task_suite import build_repo
        bodies = []

        def handler(body):
            bodies.append(body)
            return 200, {"message": {"content":
                '{"ops":[{"type":"SYMBOL_SUMMARY","path":"src/util.c"}],'
                '"strategy":"SYMBOLIC"}'}}

        base, stop = self._fake_ollama(handler)
        try:
            with mock.patch.dict("os.environ",
                                 {"SWARMSTATE_OLLAMA": base}):
                with tempfile.TemporaryDirectory(
                        prefix="ss-askfb-") as tmp:
                    root = Path(tmp)
                    build_repo(root)
                    with Orchestrator(str(root), backend="ollama",
                                      model="qwen2.5-coder:1.5b") as orch:
                        orch.ask("rename tmp to buffer",
                                 escalate=False,
                                 feedback="expected `buffer` in "
                                         "src/util.c, but it is absent")
                        joined = " ".join(
                            m["content"] for m in bodies[0]["messages"])
                        self.assertIn("did NOT achieve", joined)
                        self.assertIn("expected `buffer`", joined)

        finally:
            stop()

    def test_run_one_oracle_retry_fixes_weak(self):
        """A WEAK verdict (mechanical ok, semantic fail) triggers one
        oracle-in-the-loop re-plan fed with the reason; if the re-plan
        produces the expected state, the task flips to ok and the record
        marks oracle_retried=1."""
        import tempfile
        from types import SimpleNamespace
        import corpus as _corpus
        from task_suite import run_one

        class StubState:
            def sysinfo(self):
                return {"mem_avail_kb": 1, "load1": 1.0}
            def resource_tag(self):
                return "test"

        class StubOrch:
            state = StubState()
            last_plan_sig = "rename:tmp:buffer"
            last_retries = 0
            last_prompt_bytes = 100
            last_usage = {}
            fed = []
            received_feedback = None
            def __init__(self, root):
                self.root = root
                self.n = 0
            def ask(self, query, escalate=True, feedback=None,
                    force_model=None):
                self.n += 1
                self.received_feedback = feedback
                if self.n > 1 and feedback:
                    # the re-plan actually applies the rename
                    code = (self.root / "src" / "util.c").read_text()
                    code = code.replace("tmp", "buffer")
                    (self.root / "src" / "util.c").write_text(code)
                return "[turn 1] strategy=DELTA backend=mock " \
                       "ops=['READ','WRITE']"
            def feedback(self, ok, signature=None):
                self.fed.append((ok, signature or self.last_plan_sig))
            def other_model(self):
                return "qwen2.5-coder:7b"

        with tempfile.TemporaryDirectory(prefix="ss-oracleretry-") as tmp:
            root = Path(tmp)
            build_repo(root)
            results = root / "suite.jsonl"
            orch = StubOrch(root)
            rec = run_one(orch, root, results,
                          "rename tmp->buffer@src/util.c",
                          "rename tmp to buffer in src/util.c",
                          backend="mock")
            self.assertTrue(rec["ok"], "mechanical ok")
            self.assertTrue(rec["ok_semantic"],
                            "oracle retry fixed the edit")
            self.assertEqual(rec["oracle_retried"], 1)
            self.assertIn("expected `buffer`",
                          orch.received_feedback or "",
                          "the oracle reason was fed back")

    def test_removal_oracle_requires_preserved_structure(self):
        """A removal task must fail when the model deleted the whole
        file (observed live: lint remove unused_helper wrote 20 bytes
        and 'passed'). The survivor check makes the oracle honest."""
        import tempfile
        from task_suite import build_repo
        with tempfile.TemporaryDirectory(prefix="ss-removal-") as tmp:
            root = Path(tmp)
            build_repo(root)
            oracle = ORACLES["lint remove unused_helper@src/math.c"]
            # destructive rewrite: unused_helper gone, but so is add()
            (root / "src" / "math.c").write_text("int x = 3;\n")
            ok, reason = oracle(root, "")
            self.assertFalse(ok, "nuking the file must not pass")
            self.assertIn("int add", reason,
                          "reason tells the model what must survive")
            # correct removal: symbol gone, rest intact
            (root / "src" / "math.c").write_text(
                "#include <stdio.h>\n"
                "int x = 3;\n"
                "int add(int a, int b) { return a + b; }\n"
                "// TODO: optimize this\n")
            self.assertTrue(oracle(root, ""),
                            "symbol removed, structure preserved")

    def test_retry_hint_steers_ast_query_schema_rejections(self):
        """A schema rejection like 'pattern is required for AST_QUERY'
        (the 1.5B failure mode: it keeps emitting pattern-less AST_QUERY)
        must produce a retry hint steering to SYMBOL_SUMMARY, same as the
        invalid-node-type case."""
        from orchestrator import _retry_hint
        h = _retry_hint("op[0].pattern is required for AST_QUERY")
        self.assertIn("SYMBOL_SUMMARY", h)
        self.assertIn("AST_QUERY", h)
        self.assertEqual(_retry_hint("unrelated failure"), "")

    def test_run_one_never_scores_rejected_plan_semantic_ok(self):
        """A schema-rejected plan (mechanical FAIL) must not get a
        semantic verdict: the oracle ran nothing, so ok_semantic stays
        None and the registry must not learn 'rejected -> semantic yes'
        (the list-task tautology trap: functions exist regardless)."""
        import tempfile
        import corpus as _corpus
        from task_suite import run_one, _log_result
        from types import SimpleNamespace

        rejected_out = ("[turn 1] plan rejected (backend=ollama)\n"
                        "  schema errors: op[0].pattern is required "
                        "for AST_QUERY\n")

        class StubState:
            def sysinfo(self):
                return {"mem_avail_kb": 1, "load1": 1.0}
            def resource_tag(self):
                return "test"

        class StubOrch:
            state = StubState()
            last_plan_sig = "list:fns"
            last_retries = 1
            last_prompt_bytes = 500
            last_usage = {"prompt_tokens": 100}
            fed = []
            def ask(self, query, escalate=True):
                return rejected_out
            def feedback(self, ok, signature=None):
                # mirror Orchestrator.feedback: signature falls back to
                # the last plan signature
                self.fed.append((ok, signature or self.last_plan_sig))

        with tempfile.TemporaryDirectory(prefix="ss-runone-") as tmp:
            root = Path(tmp)
            build_repo(root)
            results = Path(tmp) / "suite.jsonl"
            orch = StubOrch()
            rec = run_one(orch, root, results, "list fns util.c",
                          "list the functions in src/util.c",
                          backend="ollama")
            self.assertFalse(rec["ok"], "rejected plan is a FAIL")
            self.assertIsNone(rec["ok_semantic"],
                              "no semantic verdict on an unexecuted plan")
            self.assertEqual(orch.fed, [(False, "list:fns")],
                             "registry learns the sig failed, never "
                             "a semantic yes for an unexecuted plan")

    def test_corpus_oracles_verify_real_repo_state(self):
        """Corpus family oracles must check the ACTUAL repo state (like the
        canonical _semantic_ok): an edit that didn't happen is WEAK, an
        edit that happened is ok. No oracle may be a tautology."""
        import tempfile
        from pathlib import Path
        # applied edit -> oracle True (full word-boundary rename, the way
        # both plan_mock and a correct model apply it)
        import re as _re
        with tempfile.TemporaryDirectory(prefix="swarmstate-corpus-") as tmp:
            root = Path(tmp)
            build_repo(root)
            code = (root / "src" / "util.c").read_text()
            (root / "src" / "util.c").write_text(
                _re.sub(r"\btmp\b", "buffer", code))
            self.assertTrue(
                ORACLES["rename tmp->buffer@src/util.c"](root, "")[0])
        # no edit -> oracle False (honest semantic check)
        with tempfile.TemporaryDirectory(prefix="swarmstate-corpus-") as tmp:
            root = Path(tmp)
            build_repo(root)
            self.assertFalse(
                ORACLES["rename tmp->buffer@src/util.c"](root, "")[0])
        # every family task has a registered oracle and a unique name
        names = [t[0] for t in ALL_TASKS]
        self.assertEqual(len(names), len(set(names)))
        for name, _q in ALL_TASKS:
            self.assertIn(name, ORACLES)

    def test_suite_semantic_ok_consults_corpus_oracles(self):
        """_semantic_ok routes corpus tasks to their per-task oracle."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="swarmstate-corpus-") as tmp:
            root = Path(tmp)
            build_repo(root)
            self.assertTrue(
                _semantic_ok("explain src/math.c", root, "(anything)")[0])
            self.assertFalse(
                _semantic_ok("rename tmp->buffer@src/util.c", root, "")[0])

    def test_plan_ollama_prompt_teaches_pattern_requirement(self):
        """The ollama system prompt must teach that AST_QUERY.pattern is
        REQUIRED and that SYMBOL_SUMMARY lists/enumerates functions —
        the two hints that make qwen fix its own bare-AST_QUERY plans."""
        captured = {}

        def handler(body):
            captured["system"] = body["messages"][0]["content"]
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        try:
            plan_ollama("list the functions in src/math.c",
                        "repo: math.c", base=base)
        finally:
            stop()
        sysp = captured["system"]
        self.assertIn("pattern is REQUIRED", sysp)
        self.assertIn("use SYMBOL_SUMMARY with path/target instead", sysp)
        self.assertIn("use it to list or", sysp)
        self.assertIn("(function_definition) @func", sysp)

    @staticmethod
    def _fake_ollama(handler, responses=None):
        """Run a fake OpenAI-compat /v1/chat/completions server in a
        thread. `handler` is a callable(body) -> (status, json_dict).
        Returns (base_url, stop)."""
        import json as _json
        import threading as _threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        import inspect as _inspect

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = _json.loads(self.rfile.read(n))
                if len(_inspect.signature(handler).parameters) >= 2:
                    status, payload = handler(self.path, body)
                else:
                    status, payload = handler(body)
                data = _json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = _threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        def _stop():
            srv.shutdown()
            srv.server_close()
        return f"http://127.0.0.1:{port}/v1", _stop

    def test_plan_ollama_parses_markdown_wrapped_plan(self):
        """Some local models wrap JSON in ```json fences — _extract_json
        must strip them (same path plan_deepseek uses)."""
        def handler(body):
            return 200, {"choices": [{"message": {"content":
                '```json\n{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}\n```'}}]}
        base, stop = self._fake_ollama(handler)
        try:
            plan = plan_ollama("status", "clean", base=base)
        finally:
            stop()
        self.assertEqual(plan["ops"][0]["type"], "STATUS")
        self.assertEqual(plan["strategy"], "DELTA")

    def test_plan_ollama_malformed_response_raises(self):
        """A gateway returning garbage must surface as a clean planner
        failure (RuntimeError from make_plan), not a crash."""
        def handler(body):
            return 200, {"choices": [{"message": {"content": "I am not JSON"}}]}
        base, stop = self._fake_ollama(handler)
        old_env = os.environ.get("SWARMSTATE_OLLAMA")
        os.environ["SWARMSTATE_OLLAMA"] = base
        try:
            with self.assertRaises(RuntimeError) as ctx:
                make_plan("status", "clean", backend="ollama")
            self.assertIn("ollama planner failed", str(ctx.exception))
        finally:
            stop()
            if old_env is None:
                os.environ.pop("SWARMSTATE_OLLAMA", None)
            else:
                os.environ["SWARMSTATE_OLLAMA"] = old_env

    def test_ollama_retry_rung_end_to_end_via_fake_endpoint(self):
        """Full orchestrator path with a fake endpoint: first plan fails
        at the kernel (rm not whitelisted), the retry rung feeds the error
        back and issues a SECOND request, which returns a valid STATUS
        plan. No real model required."""
        calls = []

        def handler(body):
            calls.append(body)
            msgs = [m.get("content", "") for m in body.get("messages", [])]
            joined = " ".join(msgs)
            if "previous plan failed" in joined:
                return 200, {"choices": [{"message": {"content":
                    '{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}'}}]}
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"EXECUTE","command":"rm -rf ."}],'
                '"strategy":"DELTA"}'}}]}

        base, stop = self._fake_ollama(handler)
        old_env = os.environ.get("SWARMSTATE_OLLAMA")
        os.environ["SWARMSTATE_OLLAMA"] = base
        try:
            with Orchestrator(self.root, backend="ollama", verbose=True) as orch:
                out = orch.ask("status")
                retried = orch.last_retries
        finally:
            stop()
            if old_env is None:
                os.environ.pop("SWARMSTATE_OLLAMA", None)
            else:
                os.environ["SWARMSTATE_OLLAMA"] = old_env
        self.assertEqual(len(calls), 2, "retry must issue a second request")
        self.assertEqual(retried, 1)
        self.assertNotIn("!!", out)
        self.assertIn("backend=ollama", out)

    def test_retry_hint_for_invalid_ast_query(self):
        """An invalid AST_QUERY pattern failure must steer the retry away
        from AST_QUERY and toward SYMBOL_SUMMARY (the doc layer's
        used_by/calls edges solve 'find unused'), so a weak local model
        does not repeat the same bad pattern."""
        from orchestrator import _retry_hint
        self.assertEqual(
            _retry_hint("ERROR: query invalid node type at offset 1 (hint: ...)"),
            " Do NOT use AST_QUERY on the retry (the previous pattern "
            "was invalid). Use SYMBOL_SUMMARY (symbol summaries with "
            "signatures and used_by/calls edges) or READ instead.")
        self.assertEqual(_retry_hint("ERROR: command not whitelisted: rm"), "")
        self.assertEqual(_retry_hint(None), "")

    def test_ollama_retry_steers_away_from_ast_query(self):
        """End-to-end: turn 1's plan uses an invalid AST_QUERY pattern
        (kernel rejects it), the retry prompt carries the hint, and the
        retried plan (SYMBOL_SUMMARY) succeeds — the local loop recovers
        without escalating."""
        calls = []

        def handler(body):
            calls.append(body)
            joined = " ".join(m.get("content", "")
                              for m in body.get("messages", []))
            if "previous plan failed" in joined:
                self.assertIn("Do NOT use AST_QUERY", joined,
                              "retry prompt carries the structural hint")
                return 200, {"choices": [{"message": {"content":
                    '{"ops":[{"type":"SYMBOL_SUMMARY","path":"src/math.c"}],'
                    '"strategy":"SYMBOLIC"}'}}]}
            return 200, {"choices": [{"message": {"content":
                '{"ops":[{"type":"READ","path":"src/math.c"},'
                '{"type":"AST_QUERY","path":"src/math.c",'
                '"pattern":"(variable_declaration (identifier) @var)"}],'
                '"strategy":"TARGETED"}'}}]}

        (Path(self.root) / "src").mkdir()
        (Path(self.root) / "src" / "math.c").write_text(
            "int add(int a, int b) { return a + b; }\n")
        base, stop = self._fake_ollama(handler)
        old_env = os.environ.get("SWARMSTATE_OLLAMA")
        os.environ["SWARMSTATE_OLLAMA"] = base
        try:
            with Orchestrator(self.root, backend="ollama", verbose=True) as orch:
                out = orch.ask("find unused variables in src/math.c",
                               escalate=False)
        finally:
            stop()
            if old_env is None:
                os.environ.pop("SWARMSTATE_OLLAMA", None)
            else:
                os.environ["SWARMSTATE_OLLAMA"] = old_env
        self.assertEqual(len(calls), 2, "retry issues a second request")
        self.assertNotIn("!!", out)
        self.assertIn("SYMBOLIC", out, "retried plan runs under SYMBOLIC")

    def test_full_dump_contains_file_contents(self):
        """The naive baseline dump must include actual file contents,
        exclude .git/.swarmstate, and stay bounded."""
        (Path(self.root) / "src").mkdir()
        (Path(self.root) / "src" / "math.c").write_text(
            "int x = 3;\nint add(int a, int b) { return a + b; }\n")
        (Path(self.root) / ".swarmstate").mkdir(exist_ok=True)
        with Orchestrator(self.root, backend="mock") as orch:
            dump = orch._full_dump()
        self.assertIn("--- src/math.c ---", dump)
        self.assertIn("int add(int a, int b)", dump)
        self.assertNotIn(".swarmstate", dump.split("=== END")[0])
        self.assertLess(len(dump), 262144)

    def test_normal_backend_uses_full_dump_and_no_escalation(self):
        """backend='normal' feeds the whole repo to the planner (naive
        baseline) and never escalates past itself."""
        (Path(self.root) / "a.c").write_text("int main(void) { return 0; }\n")
        with mock.patch("orchestrator.make_plan") as mp:
            mp.return_value = {"ops": [{"type": "STATUS"}], "strategy": "DELTA"}
            with Orchestrator(self.root, backend="normal") as orch:
                out = orch.ask("status")
        args, kwargs = mp.call_args
        self.assertIn("FULL REPOSITORY CONTENT", args[1])
        self.assertIn("int main(void)", args[1])
        self.assertIn("backend=normal", out)
        self.assertEqual(mp.call_count, 1)

    def test_deepseek_self_corrects_on_failed_plan(self):
        """backend='deepseek' gets ONE re-plan with the error fed back when
        its plan fails — it is the top of the ladder, so a single bad op
        (e.g. an invalid AST_QUERY pattern or wrong path) must not end the
        task."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.side_effect = [
                {"ops": [{"type": "EXECUTE", "command": "rm -rf ."}],
                 "strategy": "DELTA"},
                {"ops": [{"type": "STATUS"}], "strategy": "DELTA"},
            ]
            with Orchestrator(self.root, backend="deepseek") as orch:
                out = orch.ask("status")
                retried = orch.last_retries
        self.assertIn("backend=deepseek", out)
        self.assertNotIn("!!", out)
        self.assertEqual(mp.call_count, 2)
        self.assertEqual(retried, 1)
        self.assertIn("previous plan failed", mp.call_args_list[1].args[1])

    def test_symbol_summary_plan_runs_end_to_end(self):
        """The mock planner emits SYMBOL_SUMMARY for document/explain
        queries and the loop executes it through the kernel."""
        (Path(self.root) / "src").mkdir()
        (Path(self.root) / "src" / "math.c").write_text(
            "// sums a and b\n"
            "int add(int a, int b) { return a + b; }\n")
        with Orchestrator(self.root, backend="mock") as orch:
            out = orch.ask("explain what src/math.c does")
        self.assertIn("SYMBOL_SUMMARY", out)
        self.assertIn("name=add", out)
        self.assertNotIn("!!", out)

    def test_execute_command_list_is_normalized(self):
        """An LLM that emits EXECUTE command as an argv list must be
        normalized to a string before the kernel sees it (flake fix:
        'count lines' occasionally failed on ['wc', ...])."""
        plan = normalize_plan({"ops": [
            {"type": "EXECUTE",
             "command": ["wc", "-l", "src/math.c"]}],
            "strategy": "DELTA"})
        self.assertEqual(plan["ops"][0]["command"], "wc -l src/math.c")
        ok, errs = validate_plan(plan)
        self.assertTrue(ok, errs)
        # end-to-end, hermetic: the list command must flow through the
        # kernel and succeed WITHOUT escalation to the cloud
        (Path(self.root) / "src").mkdir()
        (Path(self.root) / "src" / "math.c").write_text("int a;\nint b;\n")
        with mock.patch("orchestrator.make_plan") as mp:
            mp.return_value = {"ops": [{"type": "EXECUTE",
                                        "command": ["wc", "-l", "src/math.c"]}],
                               "strategy": "DELTA"}
            with Orchestrator(self.root, backend="mock") as orch:
                out = orch.ask("count lines")
        self.assertNotIn("!!", out)
        self.assertIn("2 src/math.c", out)
        self.assertEqual(mp.call_count, 1)   # no escalation

    def test_validate_plan_joins_list_command_at_source(self):
        """_validate_plan (the real LLM path) must join an argv-list
        command into a string, never str() it to a repr — this was the
        actual 'count lines' flake: 'command not whitelisted: ['wc', '."""
        plan = _validate_plan({"ops": [
            {"type": "EXECUTE", "command": ["wc", "-l", "src/math.c"]}],
            "strategy": "DELTA"})
        self.assertEqual(plan["ops"][0]["command"], "wc -l src/math.c")
        # and the repr-string form (if a gateway already stringified it)
        # must still be rejected by the kernel, not silently passed
        plan2 = _validate_plan({"ops": [
            {"type": "EXECUTE", "command": "['wc', '-l', 'a.c']"}],
            "strategy": "DELTA"})
        self.assertEqual(plan2["ops"][0]["command"], "['wc', '-l', 'a.c']")

    def test_validate_plan_accepts_symbol_summary(self):
        ok, errs = validate_plan({"ops": [{"type": "SYMBOL_SUMMARY"}],
                                  "strategy": "DELTA"})
        self.assertTrue(ok, errs)
        ok, errs = validate_plan({"ops": [{"type": "SYMBOL_SUMMARY",
                                           "path": "a.c", "pattern": "par",
                                           "target": "parse_config"}],
                                  "strategy": "DELTA"})
        self.assertTrue(ok, errs)

    def test_fast_mode_skips_escalation(self):
        """fast=True caps the loop at ONE plan attempt: no re-plan retry
        rung and no cloud escalation, even when the local plan fails."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.return_value = {"ops": [{"type": "WRITE", "path": "/tmp/forbidden/x",
                                        "content": "x"}], "strategy": "DELTA"}
            with Orchestrator(self.root, backend="mock") as orch:
                out = orch.ask("write the fix", fast=True)
        self.assertNotIn("backend=deepseek", out)
        self.assertEqual(mp.call_count, 1)

    def test_extract_json_tolerates_llm_wrapping(self):
        self.assertEqual(
            _extract_json('```json\n{"ops":[{"type":"STATUS"}],"strategy":"DELTA"}\n```'),
            {"ops": [{"type": "STATUS"}], "strategy": "DELTA"})
        self.assertEqual(
            _extract_json('Sure! Here: {"ops":[{"type":"STATUS"}],"strategy":"DELTA"} done.'),
            {"ops": [{"type": "STATUS"}], "strategy": "DELTA"})
        # braces inside string content must not break the balance scan
        self.assertEqual(
            _extract_json('{"ops":[{"type":"WRITE","path":"x","content":"{"}]}')["ops"][0]["content"],
            "{")

    def test_validate_plan_schema_gate(self):
        ok, errs = validate_plan({"ops": [{"type": "BOGUS", "path": "x"}]})
        self.assertFalse(ok)
        self.assertTrue(any("BOGUS" in e for e in errs))
        ok, errs = validate_plan({"ops": [{"type": "READ"}]})  # missing path
        self.assertFalse(ok)
        self.assertTrue(any("path is required" in e for e in errs))
        ok, errs = validate_plan({"ops": [{"type": "GREP", "pattern": "x"}],
                                  "strategy": "NOPE"})
        self.assertFalse(ok)
        ok, errs = validate_plan(
            {"ops": [{"type": "READ", "path": "a.c"}], "strategy": "TARGETED"})
        self.assertTrue(ok, errs)
        # empty ops is invalid
        ok, errs = validate_plan({"ops": []})
        self.assertFalse(ok)

    def test_normalize_plan_accepts_context_strategy(self):
        plan = normalize_plan({"ops": [{"type": "read", "path": "a.c"}],
                               "context_strategy": "targeted"})
        self.assertEqual(plan["strategy"], "TARGETED")
        self.assertEqual(plan["ops"][0]["type"], "READ")

    def test_invalid_schema_plan_escalates(self):
        """A schema-invalid plan is rejected at the gate and re-planned via
        the cloud backend, without executing garbage."""
        with mock.patch("orchestrator.make_plan") as mp:
            mp.side_effect = [
                {"ops": [{"type": "TELEPORT", "path": "x"}], "strategy": "DELTA"},
                {"ops": [{"type": "WRITE", "path": "ok2.txt", "content": "y\n"}],
                 "strategy": "DELTA"},
            ]
            with Orchestrator(self.root, backend="mock") as orch:
                out = orch.ask("do the thing")
                self.assertEqual(orch.state.read_file("ok2.txt"), "y\n")
        self.assertIn("backend=deepseek", out)
        self.assertNotIn("TELEPORT", out)

    def test_decide_strategy_rules(self):
        self.assertEqual(
            decide_strategy([{"type": "WRITE", "path": "a", "content": "b"}]),
            ("DELTA", None))
        self.assertEqual(
            decide_strategy([{"type": "READ", "path": "x.c"}]),
            ("TARGETED", ["x.c"]))
        self.assertEqual(
            decide_strategy([{"type": "READ", "path": "x.c"}], requested="FULL"),
            ("FULL", None))
        self.assertEqual(
            decide_strategy([{"type": "READ", "path": "x.c"},
                             {"type": "STATUS"}]),
            ("DELTA", None))

    # ---- spec 9.3: registry-driven strategy selection -----------------
    def test_registry_strategy_cold_start_returns_heuristic(self):
        """No STRAT feedback yet -> the op-shape heuristic decides."""
        with SwarmState(self.root) as state:
            self.assertEqual(
                decide_strategy([{"type": "READ", "path": "x.c"}],
                                registry=state),
                ("TARGETED", ["x.c"]))
            self.assertEqual(
                decide_strategy([{"type": "WRITE", "path": "a",
                                  "content": "b"}], registry=state),
                ("DELTA", None))

    def test_registry_strategy_overrides_heuristic_to_delta(self):
        """The money case: a READ plan's heuristic default is TARGETED,
        but the registry proves DELTA wins (2 ok / 2 fail) -> the router
        overrides the heuristic and returns DELTA."""
        with SwarmState(self.root) as state:
            ops = [{"type": "READ", "path": "x.c"}]
            for _ in range(2):
                state.feedback("STRAT:READ:DELTA", True)
            for _ in range(2):
                state.feedback("STRAT:READ:TARGETED", False)
            self.assertEqual(decide_strategy(ops, registry=state),
                             ("DELTA", None))

    def test_registry_strategy_picks_proven_targeted(self):
        """DELTA proven bad twice, TARGETED proven good twice on a READ
        plan -> TARGETED with the read path as the target."""
        with SwarmState(self.root) as state:
            ops = [{"type": "READ", "path": "x.c"}]
            for _ in range(2):
                state.feedback("STRAT:READ:DELTA", False)
            for _ in range(2):
                state.feedback("STRAT:READ:TARGETED", True)
            self.assertEqual(decide_strategy(ops, registry=state),
                             ("TARGETED", ["x.c"]))

    def test_registry_strategy_targeted_without_paths_degrades(self):
        """TARGETED proven best for a WRITE-only plan: no readable paths
        to target, so it degrades to DELTA (same kernel behavior, honest
        log) instead of claiming a TARGETED that targets nothing."""
        with SwarmState(self.root) as state:
            ops = [{"type": "WRITE", "path": "a", "content": "b"}]
            for _ in range(2):
                state.feedback("STRAT:WRITE:DELTA", False)
            for _ in range(2):
                state.feedback("STRAT:WRITE:TARGETED", True)
            self.assertEqual(decide_strategy(ops, registry=state),
                             ("DELTA", None))

    def test_suite_replay_seeds_registry_cross_run(self):
        """The task suite replays prior same-backend feedback into the
        fresh registry (cross-run learning): other backends are excluded
        and legacy records without plan_sig are skipped."""
        results = Path(self.root) / "suite.jsonl"
        recs = [
            {"backend": "ollama", "plan_sig": "READ+WRITE",
             "strategy": "DELTA", "ok": True},
            {"backend": "deepseek", "plan_sig": "READ+WRITE",
             "strategy": "DELTA", "ok": False},
            {"backend": "ollama", "plan_sig": "READ",
             "strategy": "TARGETED", "ok": False},
            {"backend": "ollama", "strategy": "DELTA", "ok": True},  # legacy
            # mechanical ok but semantic fail: the router must learn FAIL
            {"backend": "ollama", "plan_sig": "AST_PARSE+AST_QUERY+READ",
             "strategy": "TARGETED", "ok": True, "ok_semantic": False},
        ]
        results.write_text("".join(json.dumps(r) + "\n" for r in recs))
        with SwarmState(self.root) as state:
            n = _replay_feedback(state, results, "ollama")
            self.assertEqual(n, 3)
            self.assertEqual(state.success_rate("STRAT:READ+WRITE:DELTA"), 1.0)
            self.assertEqual(state.success_rate("STRAT:READ:TARGETED"), 0.0)
            # semantic truth wins over mechanical ok
            self.assertEqual(
                state.success_rate("STRAT:AST_PARSE+AST_QUERY+READ:TARGETED"),
                0.0)
            self.assertEqual(
                state.registry_timing("STRAT:READ:TARGETED")["fb_samples"], 1)

    def test_decide_strategy_symbolic_for_symbol_summary(self):
        """A plan that asks for SYMBOL_SUMMARY defaults to the SYMBOLIC
        strategy (doc-layer §6.1): summaries instead of raw content."""
        self.assertEqual(
            decide_strategy([{"type": "SYMBOL_SUMMARY", "path": "x.c"}]),
            ("SYMBOLIC", None))
        self.assertEqual(
            decide_strategy([{"type": "SYMBOL_SUMMARY"}]),
            ("SYMBOLIC", None))
        # an explicit FULL request still wins
        self.assertEqual(
            decide_strategy([{"type": "SYMBOL_SUMMARY"}], requested="FULL"),
            ("FULL", None))

    def test_registry_strategy_can_pick_symbolic(self):
        """The registry can learn SYMBOLIC for a plain READ signature:
        DELTA and TARGETED proven bad, SYMBOLIC proven good -> SYMBOLIC
        overrides the op-shape heuristic default (TARGETED)."""
        with SwarmState(self.root) as state:
            for _ in range(2):
                state.feedback("STRAT:READ:DELTA", False)
                state.feedback("STRAT:READ:TARGETED", False)
                state.feedback("STRAT:READ:SYMBOLIC", True)
            self.assertEqual(
                decide_strategy([{"type": "READ", "path": "x.c"}],
                                registry=state),
                ("SYMBOLIC", None))

    def test_symbol_summary_plan_runs_symbolic_end_to_end(self):
        """The mock planner emits SYMBOL_SUMMARY for explain queries and
        the loop now runs them under the SYMBOLIC strategy."""
        (Path(self.root) / "src").mkdir()
        (Path(self.root) / "src" / "math.c").write_text(
            "// sums a and b\n"
            "int add(int a, int b) { return a + b; }\n")
        with Orchestrator(self.root, backend="mock") as orch:
            out = orch.ask("explain what src/math.c does")
        self.assertIn("strategy=SYMBOLIC", out)
        self.assertIn("name=add", out)
        self.assertNotIn("!!", out)

    def test_write_guard_rejects_shellish_content(self):
        """The kernel rejects WRITE content that looks like a shell
        command (local-model slip: sed/echo/placeholder instead of
        literal file text) and the loop surfaces the clear error."""
        with Orchestrator(self.root, backend="mock") as orch:
            out = orch.ask('write "a.txt" "s/x/y/g"', escalate=False)
            status = orch.state.status()
        self.assertIn("shell command", out)
        self.assertIn("!!", out)
        self.assertNotIn("a.txt", status)
        # legit content (with && and | inside) still writes
        with Orchestrator(self.root, backend="mock") as orch:
            out = orch.ask('write "b.txt" "int ok = a && b || c;\n"',
                           escalate=False)
            wrote = orch.state.read_file("b.txt")
        self.assertNotIn("!!", out)
        self.assertEqual(wrote, "int ok = a && b || c;\n")

    def test_registry_strategy_requires_min_samples(self):
        """A single sample is noise: 1 OK on DELTA must not flip the
        heuristic default (TARGETED for a READ plan)."""
        with SwarmState(self.root) as state:
            state.feedback("STRAT:READ:DELTA", True)
            self.assertEqual(
                decide_strategy([{"type": "READ", "path": "x.c"}],
                                registry=state),
                ("TARGETED", ["x.c"]))

    def test_registry_strategy_requested_full_still_honored(self):
        """An explicit FULL request always wins over registry evidence."""
        with SwarmState(self.root) as state:
            state.feedback("STRAT:READ:DELTA", True)
            state.feedback("STRAT:READ:DELTA", True)
            self.assertEqual(
                decide_strategy([{"type": "READ", "path": "x.c"}],
                                requested="FULL", registry=state),
                ("FULL", None))

    def test_registry_strategy_least_bad_when_all_fail(self):
        """All proven strategies bad (rate 0.0) -> least-bad wins so the
        loop keeps learning; on a full tie the cheaper strategy wins."""
        with SwarmState(self.root) as state:
            state.feedback("STRAT:READ:TARGETED", False)
            state.feedback("STRAT:READ:TARGETED", False)
            state.feedback("STRAT:READ:DELTA", False)
            state.feedback("STRAT:READ:DELTA", False)
            # 0.0 vs 0.0, same samples -> cheaper strategy (DELTA)
            self.assertEqual(registry_strategy(state, "READ", "DELTA"),
                             "DELTA")
            # one more TARGETED sample at OK flips it to TARGETED
            state.feedback("STRAT:READ:TARGETED", True)
            state.feedback("STRAT:READ:TARGETED", True)
            self.assertEqual(registry_strategy(state, "READ", "DELTA"),
                             "TARGETED")




    def test_meshbridge_fold_converges_over_wire(self):
        """The C mesh core folds the same op set to the same state hash
        regardless of which peer originated it -- the Phase 1 convergence
        invariant behind GAPS #9."""
        try:
            import meshbridge as mb
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import hashlib
        sent = []
        a = mb.Peer("alice", send=lambda b: sent.append(b))
        b = mb.Peer("bob")
        try:
            a.mutate(mb.MESH_OP_APP, "reg/STRAT/a:one:DELTA", "1")
            a.mutate(mb.MESH_OP_APP, "reg/STRAT/b:two:TARGETED", "0")
            self.assertEqual(len(sent), 2, "mutate must broadcast each op")
            for blob in sent:
                self.assertTrue(b.apply_json(blob), "remote apply accepted")
            ha = hashlib.sha256(a.state_json().encode()).hexdigest()
            hb = hashlib.sha256(b.state_json().encode()).hexdigest()
            self.assertEqual(ha, hb, "identical op set -> identical fold")
            self.assertIn("STRAT", a.state_json())
        finally:
            a.close()
            b.close()

    def test_meshbridge_apply_json_wire_format(self):
        """op_json emits the wire format the C mesh parses (verified live:
        {"op":"append","target":...,"value":...,"clock":{"counter":N,...}})."""
        try:
            import meshbridge as mb
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        blob = mb.op_json("alice", "app", "reg/STRAT/x/DELTA", "1", 3)
        op = json.loads(blob)
        self.assertEqual(op["clock"]["origin"], "alice")
        self.assertEqual(op["clock"]["counter"], 3)
        self.assertEqual(op["target"], "reg/STRAT/x/DELTA")





    def test_mesh_live_feedback_converges(self):
        """Phase 2 hookup: Orchestrator.feedback() publishes real STRAT
        samples as mesh ops; two in-process nodes fold the SAME registry
        memory (the mesh becomes a living collective memory, not a demo)."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        import tempfile
        import time
        from pathlib import Path
        import corpus
        from orchestrator import Orchestrator
        from task_suite import build_repo, run_one

        field = MeshDaemon("field", 5242, [], None)
        hub = MeshDaemon("hub", 5241, [("127.0.0.1", 5242)], None)
        field.start()
        hub.start()
        try:
            time.sleep(0.8)  # let the TCP link establish
            by_name = {n: q for n, q in corpus.ALL_TASKS}
            names = ["count src/math.c",
                     "add fn int max@src/util.c",
                     "lint remove ptr@src/util.c"]
            results = Path(tempfile.mkdtemp()) / "r.jsonl"
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_repo(root)
                with Orchestrator(str(root), backend="mock",
                                  mesh=field) as orch:
                    for name in names:
                        run_one(orch, root, results, name, by_name[name],
                                backend="mock", escalate=False, env={})
            # async propagation: poll for convergence (bounded) instead
            # of a single sleep -- the hub applies ops on its reader
            # thread, so a loaded box needs a retry window
            fstate = hstate = None
            deadline = time.time() + 6.0
            while time.time() < deadline:
                fstate = json.loads(field.peer.state_json())
                hstate = json.loads(hub.peer.state_json())
                if fstate == hstate:
                    break
                time.sleep(0.15)
            self.assertEqual(fstate, hstate,
                             "two nodes must fold identical registry memory")
            strat = fstate["reg"]["STRAT"]
            self.assertEqual(strat["EXECUTE"]["DELTA"], ["1"])
            self.assertEqual(strat["WRITE"]["DELTA"], ["1", "1"],
                             "two WRITE plans -> two samples")
        finally:
            field.shutdown()
            hub.shutdown()





    def test_mesh_registry_bridge_closes_learning_loop(self):
        """Inbound mesh ops feed the kernel registry (collective memory ->
        local router).  A peer's STRAT/MODEL learning must appear in the
        receiving node's success_rate(), and both mesh states converge."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon, registry_bridge
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import tempfile
        import time
        from pathlib import Path
        from orchestrator import Orchestrator

        a = MeshDaemon("node-a", 5321, [], None)
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(str(Path(tmp)), backend="mock")
            b = MeshDaemon("node-b", 5322, [("127.0.0.1", 5321)], None,
                           on_op=registry_bridge(orch.state))
            a.start()
            b.start()
            try:
                time.sleep(1.0)
                a.publish("reg/STRAT/WRITE/DELTA", "1")
                a.publish("reg/STRAT/READ/SYMBOLIC", "0")
                a.publish("reg/MODEL/q:abc123/qwen2.5-coder:1.5b", "1")
                time.sleep(1.5)
                self.assertEqual(
                    orch.state.success_rate("STRAT:WRITE:DELTA"), 1.0)
                self.assertEqual(
                    orch.state.success_rate("STRAT:READ:SYMBOLIC"), 0.0)
                self.assertEqual(
                    orch.state.success_rate(
                        "MODEL:q:abc123:qwen2.5-coder:1.5b"), 1.0)
                self.assertEqual(a.peer.state_json(), b.peer.state_json())
            finally:
                a.shutdown()
                b.shutdown()





    def test_registry_strategy_flips_write_to_full_on_bad_delta(self):
        """The contradictory-evidence flip (mesh_flip_demo): DELTA proven
        bad 4x and FULL proven good 2x on a WRITE plan -> the strategy
        gate refuses DELTA and selects FULL.  This is exactly what the
        mesh bridge delivers to a peer node."""
        with SwarmState(self.root) as state:
            ops = [{"type": "WRITE", "path": "a", "content": "b"}]
            for _ in range(4):
                state.feedback("STRAT:WRITE:DELTA", False)
            for _ in range(2):
                state.feedback("STRAT:WRITE:FULL", True)
            self.assertEqual(decide_strategy(ops, registry=state),
                             ("FULL", None))

    def test_mesh_bridge_accumulate_reflush_keeps_learning_across_tasks(self):
        """Per-task registries are ephemeral; the runner re-flushes the
        accumulated mesh ops into every new registry so the learning
        persists across tasks (the flip held for all 4 demo tasks)."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon, registry_bridge
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import tempfile
        import time
        from pathlib import Path
        from orchestrator import Orchestrator

        a = MeshDaemon("node-a", 5331, [], None)
        pending = []
        current = {"s": None}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def _on_op(t, v):
                pending.append((t, v))
                st = current["s"]
                if st is not None:
                    registry_bridge(st)(t, v)

            b = MeshDaemon("node-b", 5332, [("127.0.0.1", 5331)], None,
                           on_op=_on_op)
            a.start()
            b.start()
            try:
                time.sleep(1.0)
                a.publish("reg/STRAT/WRITE/DELTA", "0")
                a.publish("reg/STRAT/WRITE/DELTA", "0")
                a.publish("reg/STRAT/WRITE/DELTA", "0")
                a.publish("reg/STRAT/WRITE/DELTA", "0")
                a.publish("reg/STRAT/WRITE/FULL", "1")
                a.publish("reg/STRAT/WRITE/FULL", "1")
                time.sleep(1.0)
                # task 1: fresh registry, flush pending
                orch1 = Orchestrator(str(root), backend="mock")
                current["s"] = orch1.state
                for t, v in pending:
                    registry_bridge(orch1.state)(t, v)
                # task 2: ANOTHER fresh registry, same pending re-flush
                orch2 = Orchestrator(str(root), backend="mock")
                current["s"] = orch2.state
                for t, v in pending:
                    registry_bridge(orch2.state)(t, v)
                for orch in (orch1, orch2):
                    self.assertEqual(
                        orch.state.success_rate("STRAT:WRITE:DELTA"), 0.0)
                    self.assertEqual(
                        orch.state.success_rate("STRAT:WRITE:FULL"), 1.0)
                orch1.close()
                orch2.close()
            finally:
                a.shutdown()
                b.shutdown()





    def test_mesh_broker_backend_serves_plan_and_runs_task(self):
        """mesh:// inference broker: a provider node answers inference
        requests with a local model; the consumer orchestrates with
        backend='mesh' and executes the served plan (spec v2 6.1)."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import tempfile
        import time
        from pathlib import Path
        import corpus
        from orchestrator import Orchestrator
        from task_suite import build_repo, run_one

        prov = MeshDaemon("provider", 5601, [], None)
        prov.serve_model("qwen2.5-coder:1.5b", backend="mock")
        prov.start()
        cons = MeshDaemon("consumer", 5602, [("127.0.0.1", 5601)], None)
        cons.start()
        try:
            time.sleep(1.0)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_repo(root)
                results = Path(tmp) / "r.jsonl"
                with Orchestrator(str(root), backend="mesh",
                                  mesh=cons) as orch:
                    rec = run_one(orch, root, results, "count src/math.c",
                                  "count lines of src/math.c",
                                  backend="mesh", escalate=False, env={})
                self.assertTrue(rec["ok"] and rec["ok_semantic"],
                                "mesh-served plan must execute ok")
        finally:
            prov.shutdown()
            cons.shutdown()

    def test_mesh_broker_falls_back_to_local_on_timeout(self):
        """No provider on the mesh -> backend=mesh degrades to the local
        model (spec v2: 'falls back to local inference on timeout')."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import tempfile
        import time
        from pathlib import Path
        from orchestrator import Orchestrator
        from task_suite import build_repo, run_one

        # consumer with NO provider peer: ask() must fall back to ollama
        cons = MeshDaemon("lonely", 5603, [], None)
        cons.start()
        try:
            time.sleep(0.5)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                build_repo(root)
                results = Path(tmp) / "r.jsonl"
                with Orchestrator(str(root), backend="mesh",
                                  model="qwen2.5-coder:1.5b",
                                  mesh=cons) as orch:
                    rec = run_one(orch, root, results, "count src/math.c",
                                  "count lines of src/math.c",
                                  backend="mesh", escalate=False, env={})
                # falls back to local ollama: mechanical ok, model recorded
                self.assertTrue(rec["ok"])
        finally:
            cons.shutdown()





    # ── governance (spec v1 §6) ───────────────────────────────────────
    def _gov_run(self, mode, mock_tok=None, stdin=None,
                 secret="demo-secret", governed=("EXECUTE",),
                 task="count src/math.c"):
        """Run one governed task through the harness and return
        (rec, audit_entries, diagnostics).  diag captures everything a
        flake investigator needs: audit verdicts/details, registry
        timing for the plan signature, state hash, and timing."""
        import io as _io
        import json as _json
        import time as _time
        from governance import Governance, issue_token
        from task_suite import build_repo, run_one
        import corpus as _corpus
        by_name = {n: q for n, q in _corpus.ALL_TASKS}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_repo(root)
            log = root / "governance.log"
            gov = Governance(mode=mode, governed_ops=set(governed),
                             secret=secret, log_path=log, stdin=stdin)
            t0 = _time.monotonic()
            with Orchestrator(str(root), backend="mock", governance=gov,
                              mock_auth_token=mock_tok) as orch:
                rec = run_one(orch, root, root / "r.jsonl", task,
                              by_name[task],
                              backend="mock", escalate=False, env={})
                sig = rec.get("plan_sig") or ""
                diag = {
                    "mode": mode, "mock_tok": bool(mock_tok),
                    "task": task, "elapsed_s": round(_time.monotonic() - t0, 3),
                    "rec_ok": rec.get("ok"), "rec_sem": rec.get("ok_semantic"),
                    "write_ctx": rec.get("write_ctx"),
                    "turn": rec.get("turn", "")[:300],
                    "registry_timing": (orch.state.registry_timing(sig)
                                        if sig else {}),
                    "state_hash": orch.state.state_hash,
                }
            ents = ([_json.loads(l) for l in open(log)]
                    if log.exists() else [])
            diag["audit_verdicts"] = [e["verdict"] for e in ents]
            diag["audit_details"] = [e.get("detail") for e in ents]
            return rec, ents, diag

    def test_governance_enforce_denies_execute_without_auth(self):
        """Spec §6: an EXECUTE plan with no auth_token and no reason is
        refused; the whole plan fails and the denial is audited."""
        rec, ents, diag = self._gov_run("enforce")
        self.assertFalse(rec["ok"], f"expected FAIL: {diag}")
        self.assertEqual([e["verdict"] for e in ents], ["DENY"])
        # the gate reports the FIRST missing requirement (reason precedes
        # auth_token in the check order); either is a legitimate denial
        self.assertTrue("missing reason" in ents[0]["detail"]
                        or "missing auth_token" in ents[0]["detail"],
                        f"unexpected detail: {diag}")

    def test_governance_enforce_approves_valid_token(self):
        """A supervisor-issued token + reason -> the op executes and the
        approval is audited."""
        from governance import issue_token
        tok = issue_token("demo-secret", ttl=120)
        rec, ents, diag = self._gov_run("enforce", mock_tok=tok)
        self.assertTrue(rec["ok"] and rec["ok_semantic"],
                        f"valid token must pass: {diag}")
        self.assertEqual([e["verdict"] for e in ents], ["APPROVE"])
        self.assertEqual(ents[0]["role"], "supervisor")

    def test_governance_rejects_expired_token(self):
        """Short-lived tokens: an expired token is refused even though
        the signature is authentic."""
        from governance import issue_token
        tok = issue_token("demo-secret", ttl=-5)
        rec, ents, diag = self._gov_run("enforce", mock_tok=tok)
        self.assertFalse(rec["ok"], f"expired token must FAIL: {diag}")
        self.assertIn("expired", ents[0]["detail"])

    def test_governance_interactive_human_override(self):
        """Human override: y approves and logs HUMAN_APPROVE; n denies
        and logs HUMAN_DENY.  Both are always recorded."""
        import io as _io
        rec, ents, diag = self._gov_run("interactive",
                                        stdin=_io.StringIO("y\n"))
        self.assertTrue(rec["ok"], f"human y must pass: {diag}")
        self.assertEqual([e["verdict"] for e in ents], ["HUMAN_APPROVE"])
        self.assertEqual(ents[0]["authorizer"], "human")
        rec2, ents2, diag2 = self._gov_run("interactive",
                                           stdin=_io.StringIO("n\n"))
        self.assertFalse(rec2["ok"], f"human n must FAIL: {diag2}")
        self.assertEqual([e["verdict"] for e in ents2], ["HUMAN_DENY"])

    def test_governance_off_preserves_legacy(self):
        """mode=off: governed ops run without auth (legacy behaviour) and
        nothing is audited."""
        rec, ents, diag = self._gov_run("off")
        self.assertTrue(rec["ok"], f"off mode = legacy: {diag}")
        self.assertEqual(ents, [])

    def test_governance_token_roundtrip(self):
        """HMAC token mechanics: valid token verifies to its role;
        tampered / wrong-secret / expired / garbage tokens verify to
        None."""
        from governance import issue_token, verify_token
        t = issue_token("sec", ttl=120)
        self.assertEqual(verify_token(t, "sec"), "supervisor")
        self.assertIsNone(verify_token(t + "x", "sec"))   # tampered
        self.assertIsNone(verify_token(t, "other"))       # wrong secret
        self.assertIsNone(verify_token(issue_token("sec", ttl=-5), "sec"))
        self.assertIsNone(verify_token("garbage", "sec"))
        self.assertIsNone(verify_token("", "sec"))

    def test_governance_ungoverned_op_not_audited(self):
        """Only configured governed ops are gated: with governed_ops
        empty, an EXECUTE plan in enforce mode runs untouched."""
        rec, ents, diag = self._gov_run("enforce", governed=())
        self.assertTrue(rec["ok"], f"ungoverned must pass: {diag}")
        self.assertEqual(ents, [])

    def test_governance_reason_and_auth_checked_independently(self):
        """Deterministic gate-level proof that BOTH requirements are
        enforced no matter the check order: reason-only -> DENY for the
        missing token; token-only -> DENY for the missing reason; both ->
        APPROVE; neither -> DENY (reason reported first)."""
        import tempfile as _tf
        from governance import Governance, issue_token
        with _tf.TemporaryDirectory() as tmp:
            log = Path(tmp) / "g.log"
            gov = Governance(mode="enforce", governed_ops={"EXECUTE"},
                             secret="s", log_path=log)
            tok = issue_token("s", ttl=60)
            base = {"type": "EXECUTE", "command": "ls"}
            e_reason_only = gov.gate([{**base, "reason": "audit"}])[0]
            e_token_only = gov.gate([{**base, "auth_token": tok}])[0]
            e_both = gov.gate([{**base, "reason": "audit",
                                "auth_token": tok}])[0]
            e_neither = gov.gate([base])[0]
            self.assertEqual(e_reason_only["verdict"], "DENY")
            self.assertIn("missing auth_token", e_reason_only["detail"])
            self.assertEqual(e_token_only["verdict"], "DENY")
            self.assertIn("missing reason", e_token_only["detail"])
            self.assertEqual(e_both["verdict"], "APPROVE")
            self.assertEqual(e_neither["verdict"], "DENY")
            # reason is reported first when BOTH are missing (the most
            # actionable correction for the model)
            self.assertIn("missing reason", e_neither["detail"])
            # all four decisions are audited (the orchestrator records
            # gate output; the gate itself stays side-effect-free)
            gov.record_all([e_reason_only, e_token_only, e_both,
                            e_neither])
            rows = [json.loads(l) for l in open(log)]
            self.assertEqual([r["verdict"] for r in rows],
                             ["DENY", "DENY", "APPROVE", "DENY"])

    def test_governance_guard_replan_keeps_stamp(self):
        """Regression: the blind-write guard re-plans WRITE plans, and
        the re-planned ops must still carry the supervisor stamp.  With
        a valid token, a governed WRITE to an EXISTING file passes
        (write_ctx=1 proves the guard re-planned); before the fix this
        produced a false DENY."""
        from governance import issue_token
        tok = issue_token("demo-secret", ttl=120)
        rec, ents, diag = self._gov_run(
            "enforce", mock_tok=tok, governed=("WRITE", "EXECUTE"),
            task="add fn int max@src/util.c")
        self.assertEqual(rec.get("write_ctx"), 1,
                         "guard must have re-planned (existing file)")
        self.assertTrue(rec["ok"], f"stamped re-plan must pass: {diag}")
        self.assertEqual([e["verdict"] for e in ents], ["APPROVE"])
        self.assertEqual(ents[0]["role"], "supervisor")





    # ── federation: pools/QR-join, capability broker, LoRa ─────────────
    def test_pool_invite_crypto(self):
        """Signed expiring invites: roundtrip verifies; tampered, wrong
        secret, expired, and garbage invites verify to None."""
        from pool import issue_invite, verify_invite
        inv = issue_invite("s3cr3t", "cfs-brigade-3", "10.0.0.4:5810",
                           ttl=600)
        data = verify_invite(inv, "s3cr3t")
        self.assertIsNotNone(data)
        self.assertEqual(data["pool"], "cfs-brigade-3")
        self.assertEqual(data["hub"], "10.0.0.4:5810")
        self.assertIsNone(verify_invite(inv + "x", "s3cr3t"))   # tampered
        self.assertIsNone(verify_invite(inv, "other"))          # wrong secret
        self.assertIsNone(verify_invite(
            issue_invite("s3cr3t", "p", "h", ttl=-60), "s3cr3t"))  # expired
        self.assertIsNone(verify_invite("garbage", "s3cr3t"))

    def test_sw_cli_gov_issue_verify_audit(self):
        """sw gov: mint + verify tokens and read the audit ledger."""
        import os, subprocess, sys as _sys, json
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, SW_BACKEND="mock",
                   SWARMSTATE_LIB=str(repo / "libswarmstate.so"))
        def run(*args, **kw):
            return subprocess.run(
                [_sys.executable, str(repo / "bin" / "sw"), *args],
                capture_output=True, text=True, timeout=90, env=env,
                cwd=str(repo), **kw)
        r = run("gov", "issue", "--secret", "s1")
        self.assertEqual(r.returncode, 0, r.stderr)
        tok = r.stdout.strip().splitlines()[0]
        r2 = run("gov", "verify", "--secret", "s1", "--token", tok)
        self.assertIn("VALID", r2.stdout)
        r3 = run("gov", "verify", "--secret", "WRONG", "--token", tok)
        self.assertIn("INVALID", r3.stdout)

    def test_sw_cli_ask_governance_matrix(self):
        """sw ask governance: interactive approve -> ok + HUMAN_APPROVE
        audit; enforce without token -> DENY status + DENY audit."""
        import os, subprocess, sys as _sys, json, tempfile
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, SW_BACKEND="mock",
                   SWARMSTATE_LIB=str(repo / "libswarmstate.so"))
        # interactive approve (two stdin lines: plan confirm + approve)
        r = subprocess.run(
            [_sys.executable, str(repo / "bin" / "sw"), "ask",
             "count lines in src/math.c", "--governance", "interactive",
             "--governed-op", "EXECUTE", "--supervisor-secret", "s1"],
            input="y\ny\n", capture_output=True, text=True,
            timeout=90, env=env, cwd=str(repo))
        self.assertIn("status: ok", r.stdout, r.stderr + r.stdout)
        # enforce without token -> DENY
        r2 = subprocess.run(
            [_sys.executable, str(repo / "bin" / "sw"), "ask",
             "count lines in src/math.c", "--governance", "enforce",
             "--governed-op", "EXECUTE", "--supervisor-secret", "s1"],
            input="y\n", capture_output=True, text=True,
            timeout=90, env=env, cwd=str(repo))
        self.assertIn("status: DENY", r2.stdout, r2.stdout)
        # audit ledger records both
        r3 = subprocess.run(
            [_sys.executable, str(repo / "bin" / "sw"), "gov", "audit",
             "--tail", "20"],
            capture_output=True, text=True, timeout=60, env=env,
            cwd=str(repo))
        self.assertIn("HUMAN_APPROVE", r3.stdout)
        self.assertIn("DENY", r3.stdout)

    def test_sw_cli_ask_single_planning_pass(self):
        """sw ask shows the plan exactly ONCE (plan_callback seam kills
        the double planning pass) and an 'n' abort executes nothing."""
        import os, subprocess, sys as _sys
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, SW_BACKEND="mock",
                   SWARMSTATE_LIB=str(repo / "libswarmstate.so"))
        r = subprocess.run(
            [_sys.executable, str(repo / "bin" / "sw"), "ask",
             "count lines in src/math.c"],
            input="n\n", capture_output=True, text=True,
            timeout=90, env=env, cwd=str(repo))
        self.assertEqual(r.stdout.count("plan: strategy="), 1, r.stdout)
        self.assertIn("aborted", r.stdout)

    def test_ask_plan_callback_gates_before_execution(self):
        """plan_callback sees the FINAL plan before execution; raising
        aborts ask() with nothing executed."""
        import tempfile, pathlib as _pl
        from orchestrator import Orchestrator
        from task_suite import build_repo
        with tempfile.TemporaryDirectory() as td:
            root = _pl.Path(td)
            build_repo(root)
            seen = {}
            def cb(plan):
                seen["plan"] = plan
            orch = Orchestrator(str(root), backend="mock",
                                plan_grammar="strict")
            out = orch.ask("count lines in src/math.c",
                           plan_callback=cb)
            self.assertIn("plan", seen)
            self.assertTrue(seen["plan"]["ops"], "plan has ops")
            # abort path: raising in the callback executes nothing
            def boom(plan):
                raise RuntimeError("user aborted")
            orch2 = Orchestrator(str(root), backend="mock",
                                 plan_grammar="strict")
            before = (root / "src" / "math.c").read_bytes()
            with self.assertRaises(RuntimeError):
                orch2.ask("count lines in src/math.c", plan_callback=boom)
            after = (root / "src" / "math.c").read_bytes()
            self.assertEqual(before, after, "abort must not touch files")

    def test_sw_cli_plan_mock_backend(self):
        """sw CLI: `sw plan` with the deterministic mock backend prints a
        valid plan and ends with an ok status line (regression for the
        bin/sw harness wrapper)."""
        import os, subprocess, sys as _sys
        repo = Path(__file__).resolve().parent.parent
        env = dict(os.environ, SW_BACKEND="mock",
                   SWARMSTATE_LIB=str(repo / "libswarmstate.so"))
        r = subprocess.run(
            [_sys.executable, str(repo / "bin" / "sw"), "plan",
             "count lines in src/math.c"],
            capture_output=True, text=True, timeout=90, env=env,
            cwd=str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("strategy=", r.stdout)
        self.assertIn("status: ok", r.stdout)

    def test_identity_issue_import_show_clear(self):
        """Signed identities (UX spec, §6): issue/import roundtrip; wrong
        secret, tampered, bad role, and expired credentials refused;
        import persists chmod 600; show/clear work."""
        from identity import (issue_identity, verify_identity,
                              save_identity, load_identity)
        import stat as _stat
        import pathlib as _pl
        line = issue_identity("s3cr3t", "support", subject="bob", ttl=600)
        claims = verify_identity(line, "s3cr3t")
        self.assertEqual(claims["role"], "support")
        self.assertEqual(claims["subject"], "bob")
        self.assertIsNone(verify_identity(line + "x", "s3cr3t"))   # tampered
        self.assertIsNone(verify_identity(line, "other"))          # wrong secret
        self.assertIsNone(verify_identity("garbage", "s3cr3t"))
        with self.assertRaises(ValueError):
            issue_identity("s", "root")                            # bad role
        with self.assertRaises(ValueError):
            issue_identity("s", "field")                           # field NOT
                                                                   # integrated yet
        with self.assertRaises(ValueError):
            issue_identity("s", "support", subject="a:b")          # colon subject
        self.assertIsNone(verify_identity(
            issue_identity("s3cr3t", "support", ttl=-60), "s3cr3t"))  # expired
        with tempfile.TemporaryDirectory() as td:
            path = _pl.Path(td) / "identity.json"
            save_identity(path, line, "s3cr3t")
            self.assertEqual(_stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = load_identity(path)
            self.assertEqual(loaded["role"], "support")
            self.assertFalse(loaded["expired"])
            path.unlink()
            self.assertIsNone(load_identity(path))

    def test_sw_cli_identity_issue_import_show(self):
        """sw identity CLI: issue prints a signed line, import stores it,
        show reports the role; wrong-secret import fails; clear removes."""
        import os, subprocess, sys as _sys
        import pathlib as _pl
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, SW_BACKEND="mock",
                       SWARMSTATE_LIB=str(repo / "libswarmstate.so"),
                       SW_IDENTITY=str(_pl.Path(td) / "identity.json"))
            def run(*args, **kw):
                return subprocess.run(
                    [_sys.executable, str(repo / "bin" / "sw"), *args],
                    capture_output=True, text=True, timeout=90, env=env,
                    cwd=str(repo), **kw)
            r = run("identity", "show")
            self.assertIn("no identity", r.stdout, r.stdout)
            r = run("identity", "issue", "--role", "support",
                    "--subject", "alice", "--secret", "s1")
            self.assertEqual(r.returncode, 0, r.stderr)
            line = r.stdout.strip().splitlines()[0]
            r2 = run("identity", "import", line, "--secret", "s1")
            self.assertIn("imported support identity", r2.stdout, r2.stdout)
            r3 = run("identity", "show")
            self.assertIn("role: support", r3.stdout, r3.stdout)
            # field is not an integrated role yet
            r6 = run("identity", "issue", "--role", "field",
                     "--secret", "s1")
            self.assertIn("invalid choice", r6.stderr, r6.stderr)
            r4 = run("identity", "import", line, "--secret", "WRONG")
            self.assertIn("FAIL", r4.stdout, r4.stdout)
            r5 = run("identity", "clear")
            self.assertIn("removed identity", r5.stdout)

    def test_sw_cli_roles_dormant_no_gating(self):
        """Roles are DEFINED but DORMANT in the base: importing any
        identity (user/support/architect) must NOT restrict commands
        while sw is used as a coding harness.  Ask/plan/gov/rollback
        all run regardless of the stored role."""
        import os, subprocess, sys as _sys
        import pathlib as _pl
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, SW_BACKEND="mock",
                       SWARMSTATE_LIB=str(repo / "libswarmstate.so"),
                       SW_IDENTITY=str(_pl.Path(td) / "identity.json"))
            def run(*args, **kw):
                return subprocess.run(
                    [_sys.executable, str(repo / "bin" / "sw"), *args],
                    capture_output=True, text=True, timeout=90, env=env,
                    cwd=str(repo), **kw)
            def set_role(role):
                run("identity", "clear")
                r = run("identity", "issue", "--role", role,
                        "--subject", "t", "--secret", "s1")
                line = r.stdout.strip().splitlines()[0]
                r2 = run("identity", "import", line, "--secret", "s1")
                self.assertIn("imported", r2.stdout, r2.stdout)
            # user identity: EXECUTE plan still executes (no gating)
            set_role("user")
            r = run("ask", "count lines in src/math.c", "--yes")
            self.assertIn("status: ok", r.stdout, r.stdout)
            self.assertIn("strategy=", r.stdout, r.stdout)
            self.assertNotIn("status: DENY", r.stdout, r.stdout)
            r = run("gov", "issue", "--secret", "s1")
            self.assertIn("status: ok", r.stdout, r.stdout)
            r = run("rollback")
            self.assertIn("status: ok", r.stdout, r.stdout)
            # support and architect identities import cleanly too
            set_role("support")
            r = run("ask", "count lines in src/math.c", "--yes")
            self.assertIn("status: ok", r.stdout, r.stdout)
            set_role("architect")
            r = run("gov", "issue", "--secret", "s1")
            self.assertIn("status: ok", r.stdout, r.stdout)
            # no identity -> plain behavior, no restrictions
            run("identity", "clear")
            r = run("ask", "count lines in src/math.c", "--yes")
            self.assertIn("status: ok", r.stdout, r.stdout)

    def test_sw_cli_expired_identity_informational_only(self):
        """With gating dormant, an expired identity is reported by
        `sw identity show` but does not restrict any command."""
        import os, subprocess, sys as _sys, time as _time
        import pathlib as _pl
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, SW_BACKEND="mock",
                       SWARMSTATE_LIB=str(repo / "libswarmstate.so"),
                       SW_IDENTITY=str(_pl.Path(td) / "identity.json"))
            def run(*args, **kw):
                return subprocess.run(
                    [_sys.executable, str(repo / "bin" / "sw"), *args],
                    capture_output=True, text=True, timeout=90, env=env,
                    cwd=str(repo), **kw)
            r = run("identity", "issue", "--role", "architect",
                    "--subject", "exp", "--secret", "s1", "--ttl", "3")
            line = r.stdout.strip().splitlines()[0]
            r = run("identity", "import", line, "--secret", "s1")
            self.assertIn("imported architect identity", r.stdout, r.stdout)
            _time.sleep(3.5)
            r = run("identity", "show")
            self.assertIn("EXPIRED", r.stdout, r.stdout)
            # dormant: commands still run despite the expired identity
            r = run("ask", "count lines in src/math.c", "--yes")
            self.assertIn("status: ok", r.stdout, r.stdout)

    def test_pool_invite_single_use_per_identity(self):
        """Red-team hardening: an invite is single-use PER IDENTITY.
        Replay by a DIFFERENT node id is denied (stolen/shared invite);
        replay by the SAME id is a reconnect and stays accepted."""
        from pool import PoolHub, issue_invite

        hub = PoolHub(secret="s", name="hub")
        inv = issue_invite("s", "farm", "127.0.0.1:7100", ttl=300)
        r1 = hub.join(inv, "node1")
        self.assertIsNotNone(r1)
        # same invite, different identity -> denied
        self.assertIsNone(hub.join(inv, "node2"))
        # same invite, same identity (reconnect) -> accepted
        r3 = hub.join(inv, "node1")
        self.assertIsNotNone(r3)
        # a second invite for a second node still works
        inv2 = issue_invite("s", "farm", "127.0.0.1:7100", ttl=300)
        self.assertIsNotNone(hub.join(inv2, "node2"))

    def test_pool_join_onboards_member(self):
        """QR-join flow: a joiner presents the signed invite, the hub
        mints the allowlist entry, both push histories, and ops flow."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        import time
        from pool import issue_invite

        secret = "pool-secret-test"
        invite = issue_invite(secret, "brigade-3", "127.0.0.1:5840",
                              ttl=300)
        hub = MeshDaemon("hub", 5840, [], allow={"hub"},
                         pool_secret=secret, pool_name="brigade-3")
        node = MeshDaemon("field-1", 5841, [("127.0.0.1", 5840)],
                          allow=set(), join_invite=invite)
        hub.start()
        node.start()
        try:
            self.assertTrue(node._join_done.wait(5),
                            "joiner must complete onboarding")
            self.assertIn("field-1", hub.allow)
            self.assertIn("hub", node.allow)
            node.publish("reg/STRAT/WRITE:DELTA", "1")
            deadline = time.time() + 5
            while time.time() < deadline:
                if json.loads(hub.peer.state_json()) ==                         json.loads(node.peer.state_json()):
                    break
                time.sleep(0.15)
            self.assertEqual(json.loads(hub.peer.state_json()),
                             json.loads(node.peer.state_json()),
                             "hub must fold the joiner's op")
        finally:
            hub.shutdown()
            node.shutdown()

    def test_capability_broker_picks_by_model_and_load(self):
        """capability_announce -> broker scoring: model match wins when
        idle; a loaded preferred provider loses to an idle other; all
        loaded -> None (broadcast fallback)."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import time

        pa = MeshDaemon("prov-a", 5851, [], None)
        pa.announce_capabilities(["qwen2.5-coder:1.5b"], interval=1)
        pb = MeshDaemon("prov-b", 5852, [], None)
        pb.announce_capabilities(["qwen2.5-coder:7b"], interval=1)
        cons = MeshDaemon("consumer", 5853,
                          [("127.0.0.1", 5851), ("127.0.0.1", 5852)], None)
        pa.start(); pb.start(); cons.start()
        try:
            time.sleep(2.0)   # announces propagate
            self.assertEqual(
                cons.pick_provider(["qwen2.5-coder:7b"]), "prov-b")
            pb.sim_load = 0.9
            time.sleep(1.2)   # next announce carries the load
            self.assertEqual(
                cons.pick_provider(["qwen2.5-coder:7b"]), "prov-a")
            pa.sim_load = 0.95
            time.sleep(1.2)
            self.assertIsNone(cons.pick_provider(["qwen2.5-coder:7b"]))
        finally:
            pa.shutdown(); pb.shutdown(); cons.shutdown()

    def test_lora_link_reliable_under_loss(self):
        """Radio ARQ: messages survive 0/30/50% frame loss with
        retransmits; stats are honest (payload bytes, drops)."""
        import socket as _socket
        import threading as _threading
        import time as _time
        from lora import LoRaLink

        for loss in (0.0, 0.3, 0.5):
            link = LoRaLink(frame_cap=64, p_loss=loss, airtime=0.005,
                            seed=7)
            a = link.endpoint("A")
            b = link.endpoint("B")
            got = []
            stop = _threading.Event()

            def reader():
                while not stop.is_set():
                    try:
                        got.append(b.recv(4096))
                    except _socket.timeout:
                        continue
                    except Exception:
                        break
            _threading.Thread(target=reader, daemon=True).start()
            msgs = [b"hello over the radio", b"x" * 200,
                    b"registry:STRAT:WRITE:DELTA=0.67/6"]
            for m in msgs:
                a.sendall(m)
            _time.sleep(3.0)
            stop.set()
            self.assertEqual(sorted(got), sorted(msgs),
                             f"data lost at p_loss={loss}")
            if loss > 0:
                self.assertGreater(link.stats["retransmits"], 0,
                                   "ARQ must retransmit under loss")
            link.shutdown()

    def test_mesh_lora_full_convergence(self):
        """Two meshd nodes over the simulated radio: node A publishes a
        lora_share batch, node B reassembles + applies, states converge."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        import socket as _socket
        import threading as _threading
        import time as _time2
        from lora import LoRaLink

        link = LoRaLink(frame_cap=96, p_loss=0.2, airtime=0.005, seed=3)
        sa = link.endpoint("A")
        sb = link.endpoint("B")
        a = MeshDaemon("node-a", 0, [], None)
        b = MeshDaemon("node-b", 0, [], None)

        def reader(d, s):
            d._handle_conn(s, "lora")
        _threading.Thread(target=reader, args=(a, sa), daemon=True).start()
        _threading.Thread(target=reader, args=(b, sb), daemon=True).start()
        try:
            _time2.sleep(1.0)
            a.publish_many([("reg/STRAT/WRITE:DELTA", "1"),
                            ("reg/STRAT/WRITE:FULL", "1"),
                            ("reg/MODEL:q:abc:7b", "1")])
            deadline = _time2.time() + 10
            while _time2.time() < deadline:
                if json.loads(b.peer.state_json()) ==                         json.loads(a.peer.state_json()):
                    break
                _time2.sleep(0.2)
            self.assertEqual(json.loads(a.peer.state_json()),
                             json.loads(b.peer.state_json()),
                             "node B must fold A's radio batch")
            self.assertEqual(
                len(json.loads(b.peer.state_json()).get("reg", {}).get(
                    "STRAT", {}).get("WRITE:DELTA", [])), 1)
        finally:
            a.shutdown()
            b.shutdown()
            link.shutdown()





    # ── scoped bridges between pools (spec v1 §5) ─────────────────────
    def test_bridge_policy_scopes_relay(self):
        """BridgePolicy: target globs, data minimization, and the time
        window are enforced independently."""
        import time as _time
        from bridge import BridgePolicy
        p = BridgePolicy(allow_targets=("reg/STRAT/WRITE:*",), ttl=5)
        self.assertTrue(p.check("reg/STRAT/WRITE:DELTA"))
        self.assertFalse(p.check("reg/STRAT/READ:TARGETED"))  # sig scope
        self.assertFalse(p.check("reg/MODEL:q:x:7b"))         # minimize
        self.assertFalse(p.check("raw/some/content"))         # minimize
        p2 = BridgePolicy(allow_targets=("reg/*",), ttl=0.01)
        _time.sleep(0.05)
        self.assertTrue(p2.expired())
        self.assertFalse(p2.check("reg/STRAT/WRITE:DELTA"))   # expired

    def test_pool_bridge_relays_scoped_learning(self):
        """Two pools + one scoped bridge: WRITE strategy learning
        crosses, MODEL routing keys and other signatures do not."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        import time as _t
        from bridge import BridgePolicy, PoolBridge
        from pool import issue_invite

        SA, SB = "secret-A", "secret-B"
        inv_a = issue_invite(SA, "pool-a", "127.0.0.1:6030", ttl=600)
        inv_b = issue_invite(SB, "pool-b", "127.0.0.1:6035", ttl=600)
        ha = MeshDaemon("hub-a", 6030, [], allow={"hub-a"}, pool_secret=SA)
        fa = MeshDaemon("field-a", 6031, [("127.0.0.1", 6030)],
                        allow=set(), join_invite=inv_a)
        hb = MeshDaemon("hub-b", 6035, [], allow={"hub-b"}, pool_secret=SB)
        fb = MeshDaemon("field-b", 6036, [("127.0.0.1", 6035)],
                        allow=set(), join_invite=inv_b)
        # bridges get their OWN invites (per-device invites; an invite
        # is single-use per identity)
        ba = MeshDaemon("bridge-a", 6032, [("127.0.0.1", 6030)],
                        allow=set(),
                        join_invite=issue_invite(SA, "pool-a",
                                                 "127.0.0.1:6030", ttl=600))
        bb = MeshDaemon("bridge-b", 6037, [("127.0.0.1", 6035)],
                        allow=set(),
                        join_invite=issue_invite(SB, "pool-b",
                                                 "127.0.0.1:6035", ttl=600))
        for d in (ha, fa, hb, fb, ba, bb):
            d.start()
        try:
            for d in (ba, bb):
                self.assertTrue(d._join_done.wait(5), f"{d.name} join")
            br = PoolBridge("t-bridge", ba, bb,
                            BridgePolicy(
                                allow_targets=("reg/STRAT/WRITE/*",),
                                ttl=30))
            _t.sleep(0.5)
            fa.publish("reg/STRAT/WRITE/DELTA", "1")
            _t.sleep(1.5)
            b1 = json.loads(fb.peer.state_json())
            # multi-homing: learning crosses SOURCE-TAGGED -- it lands in
            # pool B under POOL:pool-a:STRAT:..., never in the local
            # partition (foreign learning cannot pollute local rates)
            self.assertIn(
                "DELTA",
                b1.get("reg", {}).get("POOL", {}).get("pool-a", {})
                  .get("STRAT", {}).get("WRITE", {}),
                "WRITE learning must cross the bridge (source-tagged)")
            self.assertNotIn(
                "STRAT",
                b1.get("reg", {}),
                "foreign learning must NOT land in the local partition")
            fa.publish("reg/MODEL/q:xyz/7b", "1")
            _t.sleep(1.5)
            b2 = json.loads(fb.peer.state_json())
            self.assertNotIn("MODEL", b2.get("reg", {}),
                             "MODEL routing keys must stay local")
            fa.publish("reg/STRAT/READ/TARGETED", "1")
            _t.sleep(1.5)
            b3 = json.loads(fb.peer.state_json())
            self.assertNotIn("READ:TARGETED",
                             b3.get("reg", {}).get("STRAT", {}),
                             "READ sig is outside the bridge scope")
            self.assertEqual(br.relayed, 1)
            self.assertEqual(br.denied, 2)
        finally:
            for d in (ha, fa, hb, fb, ba, bb):
                d.shutdown()





    # ── file_request / file_response (spec v1 App. B) ────────────────
    def test_safe_rel_path(self):
        """Client-supplied paths: relative-in-root only.  Absolute paths
        and '..' escapes are refused, normpath collapses are fine."""
        from meshd import _safe_rel_path
        self.assertEqual(_safe_rel_path("oracle.py"), "oracle.py")
        self.assertEqual(_safe_rel_path("conf/mesh.toml"), "conf/mesh.toml")
        self.assertEqual(_safe_rel_path("a/../b/c.py"), "b/c.py")  # stays in
        self.assertIsNone(_safe_rel_path("../etc/passwd"))
        self.assertIsNone(_safe_rel_path("a/../../etc/passwd"))
        self.assertIsNone(_safe_rel_path("/etc/passwd"))
        self.assertIsNone(_safe_rel_path(""))
        self.assertIsNone(_safe_rel_path("."))

    def test_file_exchange(self):
        """file_request/file_response: fetch, denials with reasons,
        deny-before-serve, unknown peer."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import os as _os
        import tempfile
        import time as _t

        root = tempfile.mkdtemp()
        with open(_os.path.join(root, "oracle_write.py"), "w") as fh:
            fh.write("# write oracle\nok = lambda ctx: (True, 'fine')\n")
        with open(_os.path.join(root, "big.py"), "w") as fh:
            fh.write("x = 0\n" * 30000)                       # over cap
        _os.makedirs(_os.path.join(root, "conf"))
        with open(_os.path.join(root, "conf", "mesh.toml"), "w") as fh:
            fh.write("pool = 'ward-7'\n")
        _os.makedirs(_os.path.join(root, "secret"))
        with open(_os.path.join(root, "secret", "key.pem"), "w") as fh:
            fh.write("PRIVATE KEY\n")

        a = MeshDaemon("node-a", 6130, [])
        b = MeshDaemon("node-b", 6131, [("127.0.0.1", 6130)])
        try:
            a.start(); b.start()
            _t.sleep(1.5)
            # before serving: denied with a reason, not a hang
            r0 = b.request_file("oracle_write.py", to="node-a", timeout=4)
            self.assertIs(r0.get("ok"), False)
            self.assertIn("not serving", r0.get("reason", ""))
            a.serve_files(root, allow_patterns=("*.py", "*.toml",
                                                "conf/*"), max_bytes=65536)
            _t.sleep(0.2)
            r1 = b.request_file("oracle_write.py", to="node-a", timeout=4)
            self.assertIs(r1.get("ok"), True)
            self.assertEqual(r1.get("size"), 47)
            self.assertIn("write oracle", r1.get("content", ""))
            r2 = b.request_file("conf/mesh.toml", to="node-a", timeout=4)
            self.assertIs(r2.get("ok"), True)
            self.assertIn("ward-7", r2.get("content", ""))
            self.assertIs(b.request_file("../etc/passwd",
                                         to="node-a", timeout=4).get("ok"),
                          False)
            self.assertIs(b.request_file("/etc/passwd",
                                         to="node-a", timeout=4).get("ok"),
                          False)
            r5 = b.request_file("big.py", to="node-a", timeout=4)
            self.assertIs(r5.get("ok"), False)
            self.assertIn("too large", r5.get("reason", ""))
            r6 = b.request_file("secret/key.pem", to="node-a", timeout=4)
            self.assertIs(r6.get("ok"), False)
            self.assertIn("patterns", r6.get("reason", ""))
            r7 = b.request_file("missing.py", to="node-a", timeout=4)
            self.assertIs(r7.get("ok"), False)
            self.assertIn("no such file", r7.get("reason", ""))
            with self.assertRaises(RuntimeError):
                b.request_file("x.py", to="ghost", timeout=2)
        finally:
            a.shutdown(); b.shutdown()





    # ── plan grammar / strict gate (spec v1: valid plans) ────────────
    def test_plan_grammar_validate(self):
        """Structural gate: valid plans pass; each violation class is
        caught with an actionable error naming the op and field."""
        from plan_grammar import validate, MAX_OPS
        ok, errs = validate({"ops": [{"type": "READ", "path": "a.c"}],
                             "strategy": "DELTA"})
        self.assertTrue(ok, errs)
        ok, errs = validate({"ops": [{"type": "WRITE", "path": "a.c",
                                      "content": "int x;",
                                      "line_start": 1}],
                             "strategy": "TARGETED",
                             "target_paths": ["a.c"]})
        self.assertTrue(ok, errs)
        _, errs = validate({"ops": [{"type": "BOGUS"}], "strategy": "DELTA"})
        self.assertTrue(any("type 'BOGUS' is unknown" in e for e in errs))
        _, errs = validate({"ops": [{"type": "READ"}], "strategy": "DELTA"})
        self.assertTrue(any("path is required for READ" in e for e in errs))
        _, errs = validate({"ops": [{"type": "READ", "path": "a.c"}],
                            "strategy": "MOO"})
        self.assertTrue(any("strategy 'MOO' is invalid" in e for e in errs))
        _, errs = validate({"ops": [{"type": "READ", "path": "a.c",
                                     "line_start": "one"}],
                            "strategy": "DELTA"})
        self.assertTrue(any("line_start must be an integer" in e
                            for e in errs))
        _, errs = validate({"ops": [{"type": "READ", "path": 7}],
                            "strategy": "DELTA"})
        self.assertTrue(any("path must be a string" in e for e in errs))
        _, errs = validate({"ops": [{"type": "STATUS"}] * (MAX_OPS + 1),
                            "strategy": "DELTA"})
        self.assertTrue(any("entries (max" in e for e in errs))
        ok, errs = validate("not a plan")
        self.assertFalse(ok)
        self.assertTrue(any("not a JSON object" in e for e in errs))

    def test_plan_grammar_gbnf_artifact(self):
        """The GBNF artifact enumerates every op type and strategy the
        kernel accepts -- the grammar cannot drift from the code enum."""
        from plan_grammar import PLAN_GBNF, VALID_TYPES, VALID_STRATEGIES
        self.assertGreater(len(PLAN_GBNF), 200)
        for t in VALID_TYPES:
            self.assertIn(f'\\"{t}\\"', PLAN_GBNF,
                          f"grammar misses {t}")
        for s in VALID_STRATEGIES:
            self.assertIn(f'\\"{s}\\"', PLAN_GBNF,
                          f"grammar misses {s}")

    def test_plan_grammar_strict_gate(self):
        """make_plan(grammar=...) : lenient (default) keeps historical
        best-effort behavior; strict rejects malformed plans with the
        actionable error list instead of silently dropping ops."""
        from plan_grammar import validate
        from planner import make_plan, plan_mock
        # regression: strict must ACCEPT the mock's valid output
        p = make_plan("list the TODO markers in a.c", "summary",
                      backend="mock", grammar="strict")
        self.assertTrue(validate(p)[0])
        # strict must REJECT a malformed plan end-to-end
        orig = planner_plan_mock = plan_mock
        try:
            import planner as _pl
            _pl.plan_mock = lambda q, s: {
                "ops": [{"type": "BOGUS", "path": 7}, {"type": "READ"}],
                "strategy": "MOO"}
            with self.assertRaises(ValueError) as ctx:
                make_plan("x", "y", backend="mock", grammar="strict")
            msg = str(ctx.exception)
            self.assertIn("type 'BOGUS' is unknown", msg)
            self.assertIn("path is required for READ", msg)
        finally:
            _pl.plan_mock = orig





    # ── multi-homing (federation spec: pools, partitions, multi-party) ──
    def test_multi_bridge_independence(self):
        """Two simultaneous bridges out of the same pool, each with its
        own policy and ledger: WRITE learning crosses to B only, MODEL
        learning crosses to C only, neither bridge interferes."""
        try:
            import meshbridge  # noqa: F401
            from meshd import MeshDaemon
        except OSError:
            self.skipTest("libmesh.so not built (see meshbridge.py)")
        import json
        import time as _t
        from bridge import BridgePolicy, PoolBridge
        from pool import issue_invite

        def node(name, port, hub, secret, pool):
            inv = issue_invite(secret, pool, f"127.0.0.1:{hub}", ttl=600)
            return MeshDaemon(name, port, [("127.0.0.1", hub)], allow=set(),
                              join_invite=inv)

        ha = MeshDaemon("hub-a", 6150, [], allow={"hub-a"},
                        pool_secret="SA", pool_name="farm")
        hb = MeshDaemon("hub-b", 6155, [], allow={"hub-b"},
                        pool_secret="SB", pool_name="landmark")
        hc = MeshDaemon("hub-c", 6160, [], allow={"hub-c"},
                        pool_secret="SC", pool_name="cfs")
        fa = node("field-a", 6151, 6150, "SA", "farm")
        fb = node("field-b", 6156, 6155, "SB", "landmark")
        fc = node("field-c", 6161, 6160, "SC", "cfs")
        ba = node("bridge-a", 6152, 6150, "SA", "farm")
        bb = node("bridge-b", 6157, 6155, "SB", "landmark")
        bc = node("bridge-c", 6162, 6160, "SC", "cfs")
        for d in (ha, fa, hb, fb, hc, fc, ba, bb, bc):
            d.start()
        try:
            for d in (ba, bb, bc):
                self.assertTrue(d._join_done.wait(5), f"{d.name} join")
            b1 = PoolBridge("farm-landmark", ba, bb,
                            BridgePolicy(
                                allow_targets=("reg/STRAT/WRITE/*",),
                                ttl=30))
            b2 = PoolBridge("farm-cfs", ba, bc,
                            BridgePolicy(
                                allow_targets=("reg/MODEL/*",),
                                ttl=30))
            _t.sleep(0.5)
            fa.publish("reg/STRAT/WRITE/DELTA", "1")   # b1 allows
            fa.publish("reg/MODEL/q:rescue/7b", "1")   # b2 allows
            _t.sleep(2.0)
            sb_ = json.loads(fb.peer.state_json())
            sc_ = json.loads(fc.peer.state_json())
            # WRITE crosses farm -> landmark only (source-tagged)
            self.assertIn(
                "DELTA",
                sb_.get("reg", {}).get("POOL", {}).get("farm", {})
                   .get("STRAT", {}).get("WRITE", {}))
            self.assertNotIn(
                "STRAT",
                sc_.get("reg", {}).get("POOL", {}).get("farm", {}),
                "WRITE must not leak to the wrong bridge")
            # MODEL crosses farm -> cfs only (source-tagged)
            self.assertIn(
                "7b",
                sc_.get("reg", {}).get("POOL", {}).get("farm", {})
                   .get("MODEL", {}).get("q:rescue", {}))
            self.assertNotIn(
                "MODEL",
                sb_.get("reg", {}).get("POOL", {}).get("farm", {}),
                "MODEL must not leak to the wrong bridge")
            # independent ledgers, no interference
            self.assertEqual(b1.relayed, 1)
            self.assertEqual(b1.denied, 1)   # the MODEL op b1 refused
            self.assertEqual(b2.relayed, 1)
            self.assertEqual(b2.denied, 1)   # the WRITE op b2 refused
        finally:
            for d in (ha, fa, hb, fb, hc, fc, ba, bb, bc):
                d.shutdown()

    def test_registry_bridge_pool_namespace(self):
        """registry_bridge: plain targets -> local partition keys;
        POOL-namespaced targets -> POOL:<src>:* keys (foreign, kept out
        of the local partition)."""
        from meshd import registry_bridge

        class FakeState:
            def __init__(self):
                self.keys = []
            def feedback(self, key, ok):
                self.keys.append((key, ok))

        st = FakeState()
        on_op = registry_bridge(st)
        on_op("reg/STRAT/WRITE/DELTA", "1")
        on_op("reg/MODEL/q:xyz/7b", "0")
        on_op("reg/POOL/farm/STRAT/WRITE/DELTA", "1")
        on_op("reg/POOL/farm/MODEL/q:xyz/1.5b", "1")
        self.assertIn(("STRAT:WRITE:DELTA", True), st.keys)
        self.assertIn(("MODEL:q:xyz:7b", False), st.keys)
        self.assertIn(("POOL:farm:STRAT:WRITE:DELTA", True), st.keys)
        self.assertIn(("POOL:farm:MODEL:q:xyz:1.5b", True), st.keys)
        # no raw-key collision: the namespaced key is distinct
        self.assertEqual(st.keys.count(("STRAT:WRITE:DELTA", True)), 1)

    def test_multiparty_governance(self):
        """check_multiparty: every required authority must present a
        valid token; missing or invalid authority => DENY with the
        missing list; always audited via gate_multi."""
        from governance import Governance, check_multiparty, issue_token

        secrets = {"landmark": "sec-lm", "cfs": "sec-cfs"}
        t_lm = issue_token("sec-lm", role="fleet_manager", ttl=300)
        t_cfs = issue_token("sec-cfs", role="incident_controller", ttl=300)
        ok, missing, verdicts = check_multiparty(
            {"landmark": t_lm, "cfs": t_cfs}, secrets,
            {"landmark", "cfs"})
        self.assertTrue(ok, verdicts)
        self.assertEqual(missing, [])
        # one authority missing -> denied, listed
        ok, missing, _ = check_multiparty(
            {"landmark": t_lm}, secrets, {"landmark", "cfs"})
        self.assertFalse(ok)
        self.assertIn("cfs", missing)
        # wrong-secret token -> denied
        ok, missing, _ = check_multiparty(
            {"landmark": issue_token("evil", ttl=300), "cfs": t_cfs},
            secrets, {"landmark", "cfs"})
        self.assertFalse(ok)
        self.assertIn("landmark", missing)
        # audited gate
        import tempfile
        logp = tempfile.mktemp(suffix=".jsonl")
        g = Governance(mode="enforce", log_path=logp)
        entry = g.gate_multi({"landmark": t_lm, "cfs": t_cfs}, secrets,
                             {"landmark", "cfs"},
                             plan_sig="CONTROL_VEHICLE:limit_speed",
                             summary="flood response: limit speed")
        self.assertEqual(entry["verdict"], "APPROVE")
        denied = g.gate_multi({"landmark": t_lm}, secrets,
                              {"landmark", "cfs"},
                              plan_sig="CONTROL_VEHICLE:limit_speed")
        self.assertEqual(denied["verdict"], "DENY")
        import json as _json
        lines = [l for l in open(logp).read().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("cfs=MISSING", lines[1])




    # ── plan grammar gate wired through the orchestrator ──
    def test_plan_grammar_orchestrator_strict_rejects(self):
        """Strict gate: a grammar violation inside make_plan surfaces as
        an honest planner failure (ValueError caught alongside
        RuntimeError), never a crash."""
        import tempfile as _tf
        from pathlib import Path as _P
        import orchestrator as _orch
        real = _orch.make_plan

        def boom(*a, **kw):
            raise ValueError("plan grammar violation: unknown op type "
                             "'BOGUS' (line 2)")

        _orch.make_plan = boom
        try:
            with _tf.TemporaryDirectory(prefix="ss-gate-") as d:
                orch = _orch.Orchestrator(d, backend="mock",
                                          plan_grammar="strict")
                out = orch.ask("do anything", fast=True)
                orch.close()
        finally:
            _orch.make_plan = real
        self.assertIn("planner failed", out)
        self.assertIn("plan grammar violation", out)

    def test_plan_grammar_orchestrator_passthrough(self):
        """Every make_plan call from the orchestrator carries the
        configured grammar; default stays lenient."""
        import tempfile as _tf
        import orchestrator as _orch
        calls = []
        real = _orch.make_plan

        def spy(*a, **kw):
            calls.append(kw.get("grammar"))
            return real(*a, **kw)

        _orch.make_plan = spy
        try:
            with _tf.TemporaryDirectory(prefix="ss-gate-") as d:
                orch = _orch.Orchestrator(d, backend="mock",
                                          plan_grammar="strict")
                orch.ask("add a comment header to src/util.c", fast=True)
                orch.close()
                orch2 = _orch.Orchestrator(d, backend="mock")
                orch2.ask("status", fast=True)
                orch2.close()
        finally:
            _orch.make_plan = real
        self.assertTrue(calls, "orchestrator must reach make_plan")
        self.assertTrue(all(g == "strict" for g in calls[:1]))
        self.assertEqual(calls[-1], "lenient")



if __name__ == "__main__":
    unittest.main(verbosity=2)
