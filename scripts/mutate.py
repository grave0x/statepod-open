#!/usr/bin/env python3
"""scripts/mutate.py - tiny C mutation tester (sed-style, no LLVM).

Mutates one operator at a time in a C source file, rebuilds, and runs the
test harness. Reports killed / survived / total. Built to be cheap enough
to run on small kernels as a nag autopilot layer.

Operators: arith + - * /, rel < > <= >= == !=, logical && ||, const 0->1,
return 0->1, del (line -> ;).

Usage:
  python3 scripts/mutate.py kernel.c
  python3 scripts/mutate.py --ops arith,rel kernel.c
  python3 scripts/mutate.py --limit 20 kernel.c
  python3 scripts/mutate.py --self-check
"""
from __future__ import annotations
import argparse
import json as _json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OPERATORS = {
    "arith":   (r"\s([+\-*/])\s", ["+", "-", "*", "/"]),
    "rel":     (r"\s(==|!=|<=|>=|<|>)\s", ["==", "!=", "<", ">", "<=", ">="]),
    "logical": (r"\s(&&|\|\|)\s", ["&&", "||"]),
    "const":   (r"\b(0)\b", ["1"]),
    "return":  (r"\breturn\s+0\s*;", ["return 1;"]),
}
NL = chr(10)
REPO = Path(__file__).resolve().parents[1]


def _mutate_line(line, op):
    out = []
    if op == "del":
        if line.strip() and not line.strip().startswith("//"):
            out.append(";")
        return out
    if op not in OPERATORS:
        return out
    pat, repls = OPERATORS[op]
    for m in re.finditer(pat, line):
        for r in repls:
            if r != m.group(1):
                out.append(line[:m.start(1)] + r + line[m.end(1):])
    return out


def _build_and_run(repo, mutated_src, base):
    tmp = mutated_src.parent
    obj = str(tmp / (base + ".o"))
    test_src = repo / ("test_" + base + ".c")
    run = str(tmp / "run")
    if not test_src.exists():
        return 0
    compile_cmd = (
        "gcc -O0 -I. -c " + mutated_src.name + " -o " + obj + " && "
        "gcc -O0 -I. test_" + base + ".c " + obj + " -o " + run
    )
    rc1 = subprocess.run(compile_cmd, shell=True, capture_output=True, text=True, cwd=repo).returncode
    if rc1 != 0:
        return 0
    rc2 = subprocess.run(run, capture_output=True, text=True, cwd=repo).returncode
    return 0 if rc2 == 0 else 1


def _self_check():
    ms = _mutate_line("    if (a + b > 0) {", "arith")
    assert any("a - b" in m for m in ms), ms
    ms2 = _mutate_line("    if (a == b) {", "rel")
    assert any("!=" in m for m in ms2), ms2
    ms3 = _mutate_line("    return 0;", "const")
    assert any("return 1;" in m for m in ms3), ms3
    assert _mutate_line("    foo();", "del") == [";"]
    assert _mutate_line("", "del") == []
    assert _mutate_line("    // comment", "del") == []


def run(src_path, ops=None, limit=50):
    src = Path(src_path).resolve()
    repo = src.parent.parent if src.parent.name in ("src", "lib") else src.parent
    base = src.stem
    ops = ops or list(OPERATORS.keys()) + ["del"]
    raw = src.read_text().split(NL)
    mutants = []
    killed = 0
    survived = 0
    for op in ops:
        if len(mutants) >= limit:
            break
        for li, line in enumerate(raw):
            if len(mutants) >= limit:
                break
            for ml in _mutate_line(line, op):
                if len(mutants) >= limit:
                    break
                tmp = Path(tempfile.mkdtemp(prefix="mut-"))
                mutated = tmp / src.name
                mutated.write_text(NL.join(raw[:li] + [ml] + raw[li + 1:]))
                was_killed = _build_and_run(repo, mutated, base)
                if was_killed:
                    killed += 1
                else:
                    survived += 1
                mutants.append({"op": op, "line": li + 1,
                                 "from": line.strip(), "to": ml.strip(),
                                 "killed": bool(was_killed)})
                shutil.rmtree(tmp, ignore_errors=True)
    total = killed + survived
    score = (killed / total) if total else 0.0
    return {"killed": killed, "survived": survived, "total": total,
            "score": round(score, 3), "mutants": mutants}


def main(argv=None):
    ap = argparse.ArgumentParser(description="tiny C mutation tester")
    ap.add_argument("source", nargs="?")
    ap.add_argument("--ops", default="arith,rel,logical,const,return,del")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        _self_check()
        print("OK mutate: parser + operator self-check")
        return 0
    if not args.source:
        ap.error("source file required (or --self-check)")
    ops = [o.strip() for o in args.ops.split(",") if o.strip()]
    result = run(args.source, ops=ops, limit=args.limit)
    if args.json:
        print(_json.dumps(result, indent=1))
        return 0
    print("mutation: killed=" + str(result["killed"]) +
          " survived=" + str(result["survived"]) +
          " total=" + str(result["total"]) +
          " score=" + str(result["score"]))
    for m in result["mutants"][:10]:
        flag = "KILLED" if m["killed"] else "survived"
        print("  [" + flag + "] L" + str(m["line"]) + " " + m["op"] +
              "  " + m["from"][:40] + " -> " + m["to"][:40])
    return 0


if __name__ == "__main__":
    sys.exit(main())
