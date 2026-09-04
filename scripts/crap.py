#!/usr/bin/env python3
"""scripts/crap.py - CRAP function scoring (complexity x coverage) for C/Python.

CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)
(crap4j canon; per research/crap-and-mutation-testing/narrative.md).

Usage:
  python3 scripts/crap.py kernel.c                    # C + gcov
  python3 scripts/crap.py harness/orchestrator.py     # Python (best-effort)
  python3 scripts/crap.py --threshold 50 kernel.c
  python3 scripts/crap.py --json kernel.c             # JSON to stdout
  python3 scripts/crap.py --self-check                # math self-check
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_THRESHOLD = 30.0
_BRANCH_RE = re.compile(r"\b(if|for|while|case)\b|&&|\|\||\?")
NL = chr(10)

def _complexity(body: str) -> int:
    return 1 + len(_BRANCH_RE.findall(body))

def _crap(comp: int, cov: float) -> float:
    cov = max(0.0, min(1.0, cov))
    return comp * comp * (1.0 - cov) ** 3 + comp

def split_c_funcs(src: str):
    out = []
    pat = re.compile(r"^\w[\w \t]*?\b(\w+)\s*\(", re.M)
    for m in pat.finditer(src):
        j = src.find("{", m.end())
        if j < 0 or ";" in src[m.end():j]:
            continue
        depth, k = 0, j
        while k < len(src):
            c = src[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append((m.group(1), src.count(NL, 0, j) + 1, src.count(NL, 0, k) + 1, src[j:k + 1]))
    return out

def split_py_funcs(src: str):
    out = []
    lines = src.split(NL)
    pat = re.compile(r"^(\s*)def\s+(\w+)\s*\(")
    starts = []
    for i, ln in enumerate(lines, 1):
        if pat.match(ln):
            m = pat.match(ln)
            starts.append((i, m.group(2), len(m.group(1))))
    for idx, (line_no, name, indent) in enumerate(starts):
        end = len(lines)
        for j in range(idx + 1, len(starts)):
            if starts[j][2] <= indent:
                end = starts[j][0] - 1
                break
        out.append((name, line_no, end, NL.join(lines[line_no - 1:end])))
    return out

def _coverage_c(src_path: Path, tmp: Path):
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    base = src_path.stem
    test_path = src_path.parent / ("test_" + base + ".c")
    if not test_path.exists():
        return {}
    obj = str(tmp / (base + ".o"))
    run = str(tmp / "run")
    cmd = (
        f"gcc -O0 --coverage -I. -c {src_path.name} -o {obj} && "
        f"gcc -O0 --coverage -I. {test_path.name} {obj} -o {run} && "
        f"cd {tmp} && ./run >/dev/null 2>&1; gcov -b -o {tmp} {obj}"
    )
    subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=src_path.parent)
    gcov_file = src_path.parent / (base + ".c.gcov")
    if not gcov_file.exists():
        return {}
    cov = {}
    for ln in gcov_file.read_text().splitlines():
        m = re.match(r"\s*([^:]+):\s*(\d+):", ln)
        if not m:
            continue
        cnt, line = m.group(1).strip(), int(m.group(2))
        if line == 0 or cnt == "-":
            continue
        if cnt in ("#####", "====="):
            cov[line] = 0
        elif cnt.isdigit():
            cov[line] = int(cnt)
        else:
            cov[line] = 1
    gcov_file.unlink()
    return cov

def _coverage_py(src_path: Path):
    try:
        import coverage  # type: ignore
    except ImportError:
        return {}
    tmp = Path(tempfile.mkdtemp(prefix="crap-py-"))
    cov = coverage.Coverage(data_file=str(tmp / ".coverage"), source=[str(src_path.parent)])
    cov.start()
    try:
        spec = importlib.util.spec_from_file_location(src_path.stem, src_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:
                pass
    finally:
        cov.stop()
        cov.save()
    try:
        analysis = cov.analysis2(str(src_path))
    except Exception:
        return {}
    out = {}
    for ln in analysis[1]:
        out[ln] = 1
    for ln in analysis[3]:
        out[ln] = 0
    return out

def score(src_path, threshold: float = DEFAULT_THRESHOLD):
    path = Path(src_path).resolve()
    src = path.read_text()
    if path.suffix == ".c":
        funcs = split_c_funcs(src)
        cov = _coverage_c(path, Path(tempfile.mkdtemp(prefix="crap-c-")))
    elif path.suffix == ".py":
        funcs = split_py_funcs(src)
        cov = _coverage_py(path)
    else:
        raise ValueError(f"unsupported source: {path.suffix}")
    rows = []
    for name, start, end, body in funcs:
        ex = [cov.get(L) for L in range(start, end + 1) if L in cov]
        total = len(ex)
        hit = sum(1 for x in ex if x and x > 0)
        cv = hit / total if total else 0.0
        c = _complexity(body)
        rows.append({"fn": name, "crap": _crap(c, cv), "comp": c,
                     "cov": round(cv, 3), "lines": total, "start": start})
    rows.sort(key=lambda r: -r["crap"])
    return rows

def _self_check():
    assert _crap(10, 1.0) == 10.0
    assert _crap(10, 0.0) == 110.0
    assert _complexity("{if(a){} if(b||c){while(d){}}}") == 5
    assert _crap(2, 0.5) > 2 and _crap(2, 1.0) == 2
    assert 30 < _crap(8, 0.0) < 100

def main(argv=None):
    ap = argparse.ArgumentParser(description="CRAP function scoring")
    ap.add_argument("source", nargs="?")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        _self_check()
        print("OK crap: math + parser self-check")
        return 0
    if not args.source:
        ap.error("source file required (or --self-check)")
    rows = score(args.source, args.threshold)
    if args.json:
        json.dump(rows, sys.stdout, indent=1)
        return 0
    print(f"{'CRAP':>7} {'cc':>3} {'cov%':>5} {'lns':>4}  function")
    for r in rows[: args.top]:
        flag = " <" if r["crap"] > args.threshold else ""
        print(f"{r['crap']:7.1f} {r['comp']:3d} {r['cov']*100:4.0f}% {r['lines']:4d}  {r['fn']}{flag}")
    over = [r for r in rows if r["crap"] > args.threshold]
    if rows:
        w = rows[0]
        print(f"{NL}{len(rows)} functions | CRAP>{args.threshold:.0f}: {len(over)} | worst: {w['fn']} ({w['crap']:.0f})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
