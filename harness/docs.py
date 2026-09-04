"""sw docs — documentation ingestion for SwarmState (stdlib-only).

Ingests a repo's documentation files (markdown/rst/txt/adoc/org) into a
section-level index under ~/.swarmstate/docs/<slug>/index.jsonl so the
orchestrator can serve doc summaries without reading whole files.

Design mirrors harness/research.py: pure stdlib, no deps, honest status.
Incremental: unchanged files (sha256 of content) are skipped; removed
files are pruned on the next ingest.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "0.1"
SYS = Path.home() / ".swarmstate"
DOC_EXTS = {".md", ".rst", ".txt", ".adoc", ".org"}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
             "venv", "target", "dist", "build", ".strix_runs", ".swarmstate",
             "eval", "research"}

_MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_RST_UNDER = "=-`:'\"~^_*+#"
_INDENT_CODE = re.compile(r"^( {4,}|\t)")


def list_docs(root, paths=None):
    """Find documentation files under root (or the explicit paths)."""
    root = Path(root).resolve()
    if paths:
        out = []
        for s in paths:
            p = Path(s)
            if not p.is_absolute():
                p = root / p
            if p.is_dir():
                out += [q for q in p.rglob("*")
                        if q.is_file() and q.suffix.lower() in DOC_EXTS]
            elif p.is_file():
                out.append(p)
        return sorted({q.resolve() for q in out})
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in DOC_EXTS:
                out.append(Path(dirpath) / fn)
    return sorted(out)


def _rst_heading(line, nxt):
    if not nxt or not line.strip() or len(line.strip()) < 3:
        return False
    u = nxt.rstrip()
    return (len(u) >= len(line.rstrip()) and len(set(u)) == 1
            and u[0] in _RST_UNDER and not line.startswith((" ", "\t")))


def split_sections(text):
    """Split a document into (title, [(heading, body, line_no)]).

    Markdown '#' headings, or RST underlined titles. Code blocks are
    attached to the current section.
    """
    lines = text.splitlines()
    title = ""
    sections = []
    head, body, start = "", [], 1
    in_fence = False

    def flush():
        if body or head:
            sections.append((head, "\n".join(body).strip(), start))

    for i, ln in enumerate(lines):
        if ln.startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence:
            m = _MD_HEAD.match(ln)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if m:
                flush()
                head, body, start = m.group(2).strip(), [], i + 1
                if not title and m.group(1) == "#":
                    title = head
                continue
            if (ln.strip() and not ln.startswith((" ", "\t"))
                    and _rst_heading(ln, nxt)):
                flush()
                head, body, start = ln.strip(), [], i + 1
                if not title:
                    title = head
                continue
        body.append(ln)
    flush()
    return title or "", sections


def _slug(root):
    return re.sub(r"[^a-z0-9-]+", "-",
                  Path(root).resolve().name.lower()).strip("-") or "docs"


def index_path(root):
    return SYS / "docs" / _slug(root) / "index.jsonl"


def ingest(root, out=None, force=False):
    """Index all docs under root. Returns (entries, stats)."""
    root = Path(root).resolve()
    ipath = Path(out) if out else index_path(root)
    ipath.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if ipath.exists() and not force:
        for ln in ipath.read_text(errors="replace").splitlines():
            try:
                e = json.loads(ln)
                prev[(e["path"], e["heading"], e["line"])] = e
            except Exception:
                pass
    files = list_docs(root)
    entries, seen, stats = [], set(), {"files": 0, "sections": 0, "updated": 0,
                                       "pruned": 0, "skipped": 0}
    old_keys = set(prev)
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        sha = hashlib.sha256(raw).hexdigest()
        rel = str(f.relative_to(root)) if f.is_relative_to(root) else str(f)
        seen.add(rel)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", "replace")
        title, sections = split_sections(text)
        stats["files"] += 1
        file_secs = 0
        for head, body, line in sections:
            key = (rel, head, line)
            old_keys.discard(key)
            file_secs += 1
            e = prev.get(key)
            if e and e.get("sha") == sha and not force:
                entries.append(e)
                stats["skipped"] += 1
                continue
            e = {"path": rel, "title": title, "heading": head, "line": line,
                 "sha": sha, "size": len(raw), "mtime": int(f.stat().st_mtime),
                 "tokens": len((head + " " + body).split()),
                 "preview": " ".join(body.split())[:200], "ts": int(time.time())}
            entries.append(e)
            stats["updated"] += 1
        stats["sections"] += file_secs
    stats["pruned"] = len(old_keys)
    tmp = ipath.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(e, default=str) for e in entries) + "\n")
    tmp.replace(ipath)
    return entries, stats


def load_index(root=None, path=None):
    ipath = Path(path) if path else index_path(root or Path.cwd())
    entries = []
    if ipath.exists():
        for ln in ipath.read_text(errors="replace").splitlines():
            try:
                entries.append(json.loads(ln))
            except Exception:
                pass
    return entries


_STOP = set("the a an and or of to in for on with is are be as it its this "
            "that by from at".split())


def search(entries, query, limit=10):
    """Token-overlap scoring over heading + title + preview. No deps."""
    qtoks = [t for t in re.findall(r"[a-z0-9_]+", query.lower())
             if t not in _STOP]
    if not qtoks:
        return []
    scored = []
    for e in entries:
        hay = (e["heading"] + " " + e.get("title", "") + " "
               + e.get("preview", "")).lower()
        toks = set(re.findall(r"[a-z0-9_]+", hay))
        score = sum(2 if t in e["heading"].lower() else 1
                    for t in qtoks if t in toks)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda se: (-se[0], -se[1].get("tokens", 0)))
    return [e for _, e in scored[:limit]]


def stats(entries):
    files = {}
    for e in entries:
        files.setdefault(e["path"], 0)
        files[e["path"]] += 1
    return {"files": len(files), "sections": len(entries),
            "tokens": sum(e.get("tokens", 0) for e in entries)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="sw docs")
    sub = ap.add_subparsers(dest="op")
    ig = sub.add_parser("ingest", help="(re)index docs under a repo root")
    ig.add_argument("root", nargs="?", default=".")
    ig.add_argument("--force", action="store_true")
    ig.add_argument("--out", default=None)
    for name, help_ in (("list", "list indexed files"),
                        ("stats", "index stats")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("root", nargs="?", default=".")
    se = sub.add_parser("search", help="search indexed sections")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("root", nargs="?", default=".")
    sh = sub.add_parser("show", help="print one indexed section")
    sh.add_argument("pattern", help="path or heading substring")
    sh.add_argument("root", nargs="?", default=".")
    args = ap.parse_args(argv)
    root = getattr(args, "root", None) or "."
    if args.op in (None, "stats"):
        es = load_index(root)
        print(json.dumps(stats(es), indent=1))
        return 0
    if args.op == "ingest":
        es, st = ingest(root, out=args.out, force=args.force)
        print(json.dumps(st))
        return 0
    es = load_index(root)
    if args.op == "list":
        seen = set()
        for e in es:
            if e["path"] not in seen:
                seen.add(e["path"])
                print(f"{e['title'] or e['heading'] or '?':40} {e['path']}")
        return 0
    if args.op == "search":
        for e in search(es, args.query, args.limit):
            print(f"{e['path']}:{e['line']} [{e['heading']}] {e['preview'][:120]}")
        return 0
    if args.op == "show":
        for e in es:
            if args.pattern.lower() in e["path"].lower()                     or args.pattern.lower() in e["heading"].lower():
                print(f"{e['path']}:{e['line']} [{e['heading']}]")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
