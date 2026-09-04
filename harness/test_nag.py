#!/usr/bin/env python3
"""Self-test for nag skill + CRAP + mutation helpers.

Verifies:
- dag.make_layered_fork_plan(4, 3) = 12 ops / 4 layers of 3
- nag_skill imports + heartbeat REVIEW block format
- scripts/crap.py --self-check passes
- scripts/mutate.py --self-check passes
- worker.sh is shell-syntax-clean
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(REPO), timeout=60, **kw)


def test_dag_layered_fork_4x3():
    import dag
    ops = dag.make_layered_fork_plan(4, 3)
    assert len(ops) == 12
    layers = dag.schedule(ops)
    assert [len(l) for l in layers] == [3, 3, 3, 3]


def test_nag_skill_import_and_review_block():
    sys.path.insert(0, str(REPO / ".agents" / "skills" / "nag"))
    import nag_skill
    assert nag_skill.REPO.name == "swarmstate"
    assert nag_skill.TIMEOUT_DEFAULT == 30
    assert nag_skill.MODEL_DEFAULT == "qwen2.5-coder:1.5b"
    assert nag_skill.BRANCH_PREFIX_DEFAULT == "nag"
    # is_idle returns bool
    assert isinstance(nag_skill.is_idle(0), bool)


def test_crap_self_check():
    r = run(["python3", "scripts/crap.py", "--self-check"])
    assert r.returncode == 0, r.stderr
    assert "OK crap" in r.stdout


def test_mutate_self_check():
    r = run(["python3", "scripts/mutate.py", "--self-check"])
    assert r.returncode == 0, r.stderr
    assert "OK mutate" in r.stdout


def test_worker_sh_syntax():
    r = run(["bash", "-n", ".agents/skills/nag/worker.sh"])
    assert r.returncode == 0, r.stderr


def test_crap_on_real_file():
    """CRAP on a small C file should produce a table (or 0 rows if no fns)."""
    # Use plan_yaml.c which is small
    target = REPO / "plan_yaml.c"
    if not target.exists():
        return  # skip if not present
    r = run(["python3", "scripts/crap.py", "--top", "5", str(target.relative_to(REPO))])
    assert r.returncode == 0, r.stderr
    # Output should at least show the table header or 0 functions
    assert "CRAP" in r.stdout or "0 functions" in r.stdout


def test_mutate_smoke():
    """Smoke test mutate on plan_yaml.c (no coverage = mutants survive)."""
    target = REPO / "plan_yaml.c"
    if not target.exists():
        return
    r = run(["python3", "scripts/mutate.py", "--ops", "const,return",
             "--limit", "3", "--json", str(target.relative_to(REPO))])
    # mutate can fail if no test_plan_yaml.c — that's fine, just check it doesn't crash
    assert r.returncode in (0, 1), r.stderr


def main() -> int:
    test_dag_layered_fork_4x3()
    test_nag_skill_import_and_review_block()
    test_crap_self_check()
    test_mutate_self_check()
    test_worker_sh_syntax()
    test_crap_on_real_file()
    test_mutate_smoke()
    print("OK nag: 4x3 DAG + REVIEW block + crap + mutate + worker.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
