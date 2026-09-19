#!/usr/bin/env python3
"""Strix collaboration backend for the `sp` daily harness.

`sp collaborate` wraps the local (self-hosted) Strix pentest CLI so a
statepod session can run a security scan against a repo and pull the
findings back into the statepod context for remediation. The "strix
console" is Strix's local web viewer (`strix view <run>`) -- open it with
`--console`.

No cloud login is required: this drives the `strix` binary in headless
mode (`strix -n ...`), which writes one run directory per scan under
`./strix_runs/` next to the target repo.

Run layout (per strix run):
    run.json          -- run metadata (status, timestamps, token usage)
    findings.sarif    -- SARIF 2.1.0 results (vulns + coverage notes)
    coverage.json     -- (optional) coverage surface stats
    strix.log         -- raw agent log
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_SCAN_MODE = "deep"
STRIX_RUNS = "strix_runs"


# ---------------------------------------------------------------- discovery
def find_strix() -> str | None:
    """Resolve the strix executable: env, PATH, then ~/.strix/bin."""
    env = os.environ.get("STRIX_BIN")
    if env:
        return env
    which = shutil.which("strix")
    if which:
        return which
    cand = Path.home() / ".strix" / "bin" / "strix"
    if cand.is_file():
        return str(cand)
    return None


def strix_available() -> bool:
    return find_strix() is not None


def is_local_target(target: str) -> bool:
    return Path(target).expanduser().exists()


def runs_root_for(target: str) -> Path:
    """Strix writes ./strix_runs relative to its CWD; for a local-dir
    target we run from inside the repo so the runs land next to it."""
    if is_local_target(target):
        return Path(target).expanduser().resolve() / STRIX_RUNS
    return Path.cwd() / STRIX_RUNS


def _cwd_for(target: str) -> Path:
    if is_local_target(target):
        return Path(target).expanduser().resolve()
    return Path.cwd()


def build_command(target, *, instruction=None, instruction_file=None,
                  scan_mode=None, max_budget=None, max_turns=None,
                  resume=None) -> list[str]:
    """Build the headless strix argv for a scan against `target`."""
    bin_ = find_strix() or "strix"
    starget = "." if is_local_target(target) else target
    cmd = [bin_, "-n", "--target", starget]
    if instruction:
        cmd += ["--instruction", instruction]
    if instruction_file:
        cmd += ["--instruction-file", instruction_file]
    if scan_mode:
        cmd += ["--scan-mode", scan_mode]
    if max_budget:
        cmd += ["--max-budget", str(max_budget)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if resume:
        cmd += ["--resume", resume]
    return cmd


# -------------------------------------------------------------------- runs
def list_runs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    runs = [p for p in root.iterdir()
            if p.is_dir() and (p / "run.json").is_file()]
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return runs


def latest_run(root: Path) -> Path | None:
    runs = list_runs(root)
    return runs[0] if runs else None


def run_by_name(root: Path, name: str) -> Path | None:
    for p in list_runs(root):
        if p.name == name:
            return p
    return None


def read_run(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / "run.json").read_text())
    except Exception:
        return {}


# --------------------------------------------------------------- findings
_LEVEL_ORDER = {"error": 0, "warning": 1, "note": 2, "none": 3}


def _severity(level: str) -> str:
    return {"error": "high", "warning": "medium",
            "note": "low", "none": "info"}.get(level, "info")


def findings_from_run(run_dir: Path) -> list[dict]:
    """Parse findings.sarif into flat finding dicts (empty on absence)."""
    sarif = run_dir / "findings.sarif"
    if not sarif.is_file():
        return []
    try:
        doc = json.loads(sarif.read_text())
    except Exception:
        return []
    out: list[dict] = []
    for run in doc.get("runs", []):
        for r in run.get("results", []):
            props = (r.get("properties") or {}).get("strix", {}) or {}
            surface = props.get("surface") or ""
            if not surface:
                for loc in r.get("locations", []):
                    lls = loc.get("logicalLocations") or []
                    if lls:
                        surface = lls[0].get("fullyQualifiedName", "")
                        break
            out.append({
                "rule_id": r.get("ruleId", ""),
                "level": r.get("level", "none"),
                "severity": _severity(r.get("level", "none")),
                "kind": r.get("kind", ""),
                "message": (r.get("message") or {}).get("text", "").strip(),
                "surface": surface,
                "risk_area": props.get("risk_area", ""),
                "coverage_outcome": props.get("coverage_outcome", ""),
                "recorded_by": props.get("recorded_by", ""),
                "source": props.get("source", ""),
            })
    out.sort(key=lambda f: _LEVEL_ORDER.get(f["level"], 9))
    return out


def coverage_summary(run_dir: Path) -> dict | None:
    cov = run_dir / "coverage.json"
    if not cov.is_file():
        return None
    try:
        return json.loads(cov.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------- summary
def summarize(run_dir: Path) -> str:
    """Render a markdown findings/coverage summary for a run directory."""
    meta = read_run(run_dir)
    findings = findings_from_run(run_dir)
    cov = coverage_summary(run_dir)
    status = meta.get("status", "unknown")
    lines = [f"# Strix findings — {run_dir.name}", "",
             f"- status: `{status}`",
             f"- run dir: `{run_dir}`"]
    if meta.get("start_time"):
        lines.append(f"- started: {meta.get('start_time')}")
    if meta.get("end_time"):
        lines.append(f"- ended: {meta.get('end_time')}")
    usage = meta.get("llm_usage") or {}
    if usage.get("requests"):
        lines.append(f"- llm: {usage.get('requests')} requests, "
                     f"{usage.get('total_tokens', 0):,} tokens")
    if cov:
        summ = cov.get("summary") or {}
        comp = cov.get("completeness") or {}
        lines += ["", "## coverage",
                  f"- surfaces_reviewed={summ.get('surfaces_reviewed', 0)} "
                  f"findings_filed={summ.get('findings_filed', 0)} "
                  f"gaps={summ.get('gaps', 0)}",
                  f"- complete={comp.get('complete', False)} "
                  f"scan_status={comp.get('scan_status', 'unknown')}"]
        for c in comp.get("caveats", []):
            lines.append(f"  - caveat: {c}")
        for g in cov.get("gaps", []):
            who = g.get("agent_name") or g.get("agent_id") or ""
            label = " | ".join(x for x in (who, g.get("risk_area", ""),
                                           g.get("surface", "")) if x)
            lines.append(f"  - gap [{g.get('kind', '')}] {label}: "
                         f"{g.get('detail', '')}")
    lines += ["", f"## findings ({len(findings)})", ""]
    if not findings:
        lines.append("(no findings recorded — `results` empty in SARIF)")
    for f in findings:
        head = f["rule_id"] or f["risk_area"] or "finding"
        lines.append(f"### [{f['severity']}] {head}")
        if f["surface"]:
            lines.append(f"- surface: `{f['surface']}`")
        if f["risk_area"] and f["risk_area"] != head:
            lines.append(f"- risk area: {f['risk_area']}")
        if f["coverage_outcome"]:
            lines.append(f"- outcome: {f['coverage_outcome']}")
        if f["recorded_by"]:
            lines.append(f"- recorded by: {f['recorded_by']}")
        if f["message"]:
            lines.append(f"- {f['message']}")
        lines.append("")
    return "\n".join(lines)


def findings_hint(run_dir: Path, n: int) -> str:
    """One-line remediation pointer that feeds the planner."""
    if n == 0:
        return (f"Strix run {run_dir.name} recorded no findings; "
                f"nothing queued for remediation.")
    return (f"{n} Strix finding(s) in {run_dir.name} — "
            f"run `sp ask \"remediate the Strix findings in {run_dir}\"`")


# ----------------------------------------------------------------- launch
def launch_console(run_dir: Path) -> subprocess.Popen | None:
    """Open Strix's local web console (`strix view`) without stealing
    focus: serve on 127.0.0.1 and print the URL. Returns the server proc."""
    bin_ = find_strix()
    if not bin_:
        return None
    # `strix view` resolves runs under ./strix_runs relative to its CWD,
    # so run it from the repo root (the grandparent of the run dir).
    log = run_dir.parent / f"{run_dir.name}.view.log"
    with open(log, "a") as fh:
        proc = subprocess.Popen(
            [bin_, "view", run_dir.name, "--no-open"],
            cwd=str(run_dir.parent.parent), stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True)
    return proc


def launch_scan(target, *, instruction=None, instruction_file=None,
                scan_mode=None, max_budget=None, max_turns=None,
                resume=None, detach=False):
    """Launch the scan. detach=True backgrounds it (nohup-style) and
    returns (None, log_path); otherwise returns the foreground Popen."""
    cmd = build_command(target, instruction=instruction,
                        instruction_file=instruction_file,
                        scan_mode=scan_mode, max_budget=max_budget,
                        max_turns=max_turns, resume=resume)
    cwd = _cwd_for(target)
    runs = runs_root_for(target)
    runs.mkdir(parents=True, exist_ok=True)
    log = runs / "collaborate.log"
    if detach:
        with open(log, "a") as fh:
            fh.write(f"# {' '.join(cmd)}\n")
            proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=fh,
                                    stderr=subprocess.STDOUT,
                                    start_new_session=True)
        return None, log
    proc = subprocess.Popen(cmd, cwd=str(cwd), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc, log
