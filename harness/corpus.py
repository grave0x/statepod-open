"""Corpus: parameterized task families + deterministic semantic oracles.

The canonical 10-task suite proves the plumbing; this module grows the
SAMPLE COUNT the §9.3 registry learns from. Every family task is a
(unique_name, query) pair with a registered oracle in ORACLES that checks
the repo state AFTER the run (the same discipline as the canonical
suite's _semantic_ok: mechanical ok is not enough).

Design rules:
- oracles are deterministic and cheap (string/regex checks on the
  fixture files); no compilation, no LLM calls.
- tasks that share an op shape share a plan signature in the registry
  automatically (plan_sig comes from the ops), so parameterized variants
  accumulate STRAT: samples for the same signature.
- FIXTURE is the single source of truth for the scratch repo: build_repo
  writes it, plan_mock's edit engine reads it, oracles check it. Keep the
  three in sync.
"""
import re
from pathlib import Path

# ----------------------------------------------------------------------
# Fixture (single source of truth for the scratch repo)
# ----------------------------------------------------------------------
FIXTURE = {
    "src/math.c": (
        "#include <stdio.h>\n"
        "int x = 3;\n"
        "int add(int a, int b) { return a + b; }\n"
        "// TODO: optimize this\n"
        "int unused_helper(void) { return 42; }\n"
    ),
    "src/util.c": (
        "int tmp = 0;\n"
        "int mul(int a, int b) { return a * b; }\n"
        "int *ptr = &tmp;\n"
        "// FIXME: leaks\n"
    ),
    "src/parse.c": (
        "char* buf = 0;\n"
        "int parse(const char* s) { return s ? 1 : 0; }\n"
        "// HACK: uninitialized\n"
    ),
    "src/app.py": (
        "def parse_config(cfg):\n"
        "    return cfg\n"
        "# NOTE: keep in sync with parse.c\n"
    ),
    "README.md": "# demo repo\nTODO: fill me\n",
}

C_FILES = ["src/math.c", "src/util.c", "src/parse.c"]

# ----------------------------------------------------------------------
# Oracles: task name -> callable(root: Path, out: str) -> bool
# ----------------------------------------------------------------------
ORACLES = {}


def _read(root: Path, path: str) -> str:
    p = root / path
    return p.read_text() if p.exists() else ""


def _word_boundary(s: str) -> str:
    return rf"\b{re.escape(s)}\b"


def _oracle_rename(old: str, new: str, path: str):
    """Oracle contract: (root, out) -> (ok: bool, reason: str). The
    reason feeds the oracle-in-the-loop retry rung, so it must tell the
    model what state to produce (not just "failed")."""
    def check(root: Path, out: str) -> tuple:
        code = _read(root, path)
        if re.search(_word_boundary(new), code) is None:
            return (False, f"expected `{new}` in {path} (rename from "
                    f"`{old}`), but it is absent")
        if re.search(_word_boundary(old), code) is not None:
            return (False, f"`{old}` is still present in {path}; it "
                    f"must be renamed to `{new}` everywhere")
        return (True, "")
    return check


def _oracle_contains(needle: str, path: str):
    def check(root: Path, out: str) -> tuple:
        if needle in _read(root, path):
            return (True, "")
        return (False, f"expected `{needle}` in {path}, but it is absent")
    return check


def _oracle_absent(needle: str, path: str, survivors: tuple = ()):
    """The symbol is gone AND (when survivors given) the rest of the
    fixture survived. Without the survivor check, a model that deleted
    the WHOLE file scores 'ok' on a removal task (observed live:
    lint remove unused_helper wrote a 20-byte file that passed)."""
    def check(root: Path, out: str) -> tuple:
        code = _read(root, path)
        if re.search(_word_boundary(needle), code) is not None:
            return (False, f"`{needle}` is still present in {path}; "
                    f"remove it")
        for s in survivors:
            if re.search(_word_boundary(s), code) is None:
                return (False, f"removing `{needle}` must keep `{s}` "
                        f"in {path} — do NOT delete unrelated code")
        return (True, "")
    return check


def _oracle_replace(old: str, new: str, path: str):
    def check(root: Path, out: str) -> tuple:
        code = _read(root, path)
        if new in code and old not in code:
            return (True, "")
        if new not in code:
            return (False, f"expected `{new}` in {path}, but it is absent")
        return (False, f"`{old}` is still present in {path}; replace it "
                f"with `{new}`")
    return check


def _oracle_readonly(path: str):
    """Read-only tasks (explain/list/grep/diff/count/status): nothing to
    verify, but keep the (ok, reason) contract."""
    def check(root: Path, out: str) -> tuple:
        return (True, "")
    return check


def _reg(name, query, oracle):
    ORACLES[name] = oracle
    return (name, query)


# ----------------------------------------------------------------------
# Families (each generator yields Task tuples; names are unique)
# ----------------------------------------------------------------------
FAMILY_TASKS: list[tuple[str, str]] = []


def _family(name: str, query: str, oracle) -> None:
    FAMILY_TASKS.append(_reg(name, query, oracle))


def _build_families() -> None:
    # -- rename variable: (old, new, file) -----------------------------
    for old, new, path in [("x", "count", "src/math.c"),
                           ("x", "width", "src/math.c"),
                           ("tmp", "buffer", "src/util.c"),
                           ("buf", "buffer", "src/parse.c"),
                           ("ptr", "phead", "src/util.c")]:
        _family(f"rename {old}->{new}@{path}",
                f'rename "{old}" to "{new}" in {path}',
                _oracle_rename(old, new, path))

    # -- add function: (signature, file) -------------------------------
    for sig, path in [("int max(int a, int b)", "src/util.c"),
                      ("void init(void)", "src/parse.c"),
                      ("double mean(double* arr, int n)", "src/math.c"),
                      ("int clamp(int v, int lo, int hi)", "src/util.c")]:
        _family(f"add fn {sig.split('(')[0]}@{path}",
                f'add a function "{sig}" to {path}',
                _oracle_contains(sig, path))

    # -- fix lint: remove unused symbol --------------------------------
    for kind, name, path, survivors in [
            ("function", "unused_helper", "src/math.c",
             ("int add", "int x")),
            ("variable", "buf", "src/parse.c", ("int parse",)),
            ("variable", "ptr", "src/util.c",
             ("int mul", "int tmp"))]:
        _family(f"lint remove {name}@{path}",
                f'remove the unused {kind} "{name}" from {path}',
                _oracle_absent(name, path, survivors))

    # -- explain code (read-only; oracle always true) ------------------
    for path in C_FILES:
        _family(f"explain {path}", f"explain what {path} does",
                _oracle_readonly(path))

    # -- list functions (read-only) ------------------------------------
    _family("list fns util.c", "list the functions in src/util.c",
            _oracle_readonly("src/util.c"))
    _family("list fns repo-wide", "list all functions in the repository",
            _oracle_readonly("repo"))
    _family("list fns pattern parse", 'list the functions matching "parse" in src/',
            _oracle_readonly("repo"))

    # -- find TODO variants --------------------------------------------
    for pat in ("TODO", "FIXME", "HACK", "NOTE"):
        _family(f"grep {pat}", f"grep '{pat}'", _oracle_readonly("repo"))

    # -- diff / count on different files (read-only) -------------------
    for path in C_FILES + ["README.md"]:
        _family(f"diff {path}", f"diff {path}", _oracle_readonly("repo"))
        _family(f"count {path}", f"count lines of {path}",
                _oracle_readonly("repo"))

    # -- write note variants (content checks) --------------------------
    for label, content, path in [("notes", "meeting notes\n", "notes.md"),
                                ("changelog", "v1.0 released\n", "CHANGELOG.md"),
                                ("notes-unicode", "\u65e5\u672c\u8a9e\u30e1\u30e2\n", "notes.md")]:
        _family(f"write {label}",
                f'write "{path}" "{content}"',
                _oracle_contains(content.strip(), path))

    # -- update README -------------------------------------------------
    for text in ("usage: parse_config(cfg)", "build: make test-all",
                 "license: MIT"):
        _family(f"readme {text.split(':')[0]}",
                f'add "{text}" to README.md',
                _oracle_contains(text, "README.md"))

    # -- add unit test -------------------------------------------------
    for name, path in [("test_mul", "src/util.c"), ("test_parse", "src/parse.c")]:
        _family(f"test {name}@{path}",
                f'add a test function "{name}" to {path}',
                _oracle_contains(name, path))

    # -- add docstring -------------------------------------------------
    for name, path in [("add", "src/math.c"), ("mul", "src/util.c")]:
        _family(f"docstring {name}@{path}",
                f'add a doc comment to the function "{name}" in {path}',
                _oracle_contains(f"// {name}", path))

    # -- style conversion ----------------------------------------------
    for old, new, path in [("int *ptr", "int* ptr", "src/util.c"),
                           ("char* buf", "char *buf", "src/parse.c")]:
        _family(f"style {old}->{new}@{path}",
                f'convert "{old}" to "{new}" in {path}',
                _oracle_replace(old, new, path))


_build_families()

FAMILIES = {  # family name -> list of (task_name, query)
    "rename": [t for t in FAMILY_TASKS if t[0].startswith("rename ")],
    "add_function": [t for t in FAMILY_TASKS if t[0].startswith("add fn ")],
    "fix_lint": [t for t in FAMILY_TASKS if t[0].startswith("lint ")],
    "explain": [t for t in FAMILY_TASKS if t[0].startswith("explain ")],
    "list_functions": [t for t in FAMILY_TASKS if t[0].startswith("list ")],
    "find_todo": [t for t in FAMILY_TASKS if t[0].startswith("grep ")],
    "diff": [t for t in FAMILY_TASKS if t[0].startswith("diff ")],
    "count": [t for t in FAMILY_TASKS if t[0].startswith("count ")],
    "write": [t for t in FAMILY_TASKS if t[0].startswith("write ")],
    "readme": [t for t in FAMILY_TASKS if t[0].startswith("readme ")],
    "test": [t for t in FAMILY_TASKS if t[0].startswith("test ")],
    "docstring": [t for t in FAMILY_TASKS if t[0].startswith("docstring ")],
    "style": [t for t in FAMILY_TASKS if t[0].startswith("style ")],
}

ALL_TASKS = FAMILY_TASKS  # flat corpus pool (44 tasks)
