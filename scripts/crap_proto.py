#!/usr/bin/env python3
"""PROTOTYPE - scripts/crap_proto.py: CRAP scores for kernel.c functions.

CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)   [crap4j / Wikipedia canon]
comp = cyclomatic complexity (branch-point count), cov = gcov line coverage.

Question this prototype answers: does a cheap CRAP ranking over kernel.c
(regex complexity + gcov line coverage) surface real test-priority targets?

Usage:  python3 scripts/crap_proto.py [source.c]   (default kernel.c)
Build:  compiles a --coverage binary with the repo test harness in /tmp.
"""
import json, os, re, shutil, subprocess as sp, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BR = re.compile(r"\b(if|for|while|case)\b|&&|\|\||\?")
THRESH = 30.0  # classic crap4j review bar

def split_funcs(src):
    out = []
    pat = re.compile(r"^\w[\w \t\*]*?\b(\w+)\s*\(", re.M)
    for m in pat.finditer(src):
        j = src.find("{", m.end())
        if j < 0 or ";" in src[m.end():j]:
            continue
        depth, k = 0, j
        while k < len(src):
            c = src[k]
            if c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0: break
            k += 1
        out.append((m.group(1), src.count("\n", 0, j) + 1, src.count("\n", 0, k) + 1, src[j:k+1]))
    return out

def comp(body):
    return 1 + len(BR.findall(body))

def crap(c, cov):
    return c * c * (1.0 - max(0.0, min(1.0, cov)))**3 + c

def coverage(src_path, tmp="/tmp/crap-proto"):
    """Instrumented build + run + gcov. Returns {line: hit(0/1)} for src_path."""
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    base = os.path.splitext(os.path.basename(src_path))[0]
    test = "test_" + base + ".c"
    r = sp.run(
        f"gcc -O0 --coverage -I. -c {base}.c -o {tmp}/{base}.o && "
        f"gcc -O0 --coverage -I. {test} {tmp}/{base}.o -o {tmp}/run && "
        f"cd {tmp} && ./run >/dev/null 2>&1; gcov -b -o {tmp} {tmp}/{base}.o",
        shell=True, capture_output=True, text=True, cwd=ROOT)
    gcov_file = os.path.join(ROOT, base + ".c.gcov")
    if not os.path.exists(gcov_file):
        sys.exit("gcov failed: " + (r.stderr or r.stdout)[-400:])
    cov = {}
    for l in open(gcov_file):
        m = re.match(r"\s*([^:]+):\s*(\d+):", l)
        if not m: continue
        cnt, ln = m.group(1).strip(), int(m.group(2))
        if ln == 0 or cnt == "-": continue
        cov[ln] = 0 if cnt in ("#####", "=====") else (int(cnt) if cnt.isdigit() else 1)
    os.remove(gcov_file)
    return cov

def main():
    src_path = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 1 else "kernel.c")
    cov = coverage(src_path)
    src = open(src_path).read()
    rows = []
    for n, s, e, body in split_funcs(src):
        ex = [cov.get(L) for L in range(s, e + 1) if L in cov]
        tot = len(ex)
        hit = sum(1 for x in ex if x and x > 0)
        cv = hit / tot if tot else 0.0
        c = comp(body)
        rows.append({"fn": n, "crap": crap(c, cv), "comp": c,
                     "cov": round(cv, 3), "lines": tot, "start": s})
    rows.sort(key=lambda r: -r["crap"])
    print(f'{"CRAP":>7} {"cc":>3} {"cov%":>5} {"lns":>4}  function')
    for r in rows[:20]:
        flag = " <" if r["crap"] > THRESH else ""
        print(f'{r["crap"]:7.1f} {r["comp"]:3d} {r["cov"]*100:4.0f}% {r["lines"]:4d}  {r["fn"]}{flag}')
    over = [r for r in rows if r["crap"] > THRESH]
    print(f"\n{len(rows)} functions | CRAP>{THRESH:.0f}: {len(over)} | "
          f"worst: {rows[0]['fn']} ({rows[0]['crap']:.0f})")
    json.dump(rows, open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "crap.json"), "w"), indent=1)
    assert crap(10, 1.0) == 10.0 and crap(10, 0.0) == 110.0
    assert comp("{if(a){} if(b||c){while(d){}}}") == 5

if __name__ == "__main__":
    main()
