"""Plan grammar (spec v1: "the model must produce a valid plan").

Two views of the SAME schema:
  - PLAN_GBNF: llama.cpp GBNF that CONSTRAINS generation -- a
    grammar-capable backend literally cannot emit a structurally
    invalid plan (wrong shape, unknown op type, bad strategy).
  - validate(): pure-Python structural mirror for backends without
    grammar support (mock, DeepSeek, ollama OpenAI-compat) -- reject
    with actionable errors instead of silently dropping malformed ops.

The kernel remains the final authority (schema/allowlist/metachar
checks); this layer only keeps malformed plans from reaching it.
"""
from __future__ import annotations

# op-type enum (must mirror planner.VALID_TYPES -- a test asserts the
# grammar never drifts from the kernel enum)
VALID_TYPES = ("READ", "WRITE", "GREP", "DIFF", "STATUS", "EXECUTE",
               "AST_PARSE", "AST_QUERY", "SYMBOL_SUMMARY")
VALID_STRATEGIES = ("DELTA", "TARGETED", "FULL", "SYMBOLIC")

# required fields per op type (mirror planner.PLAN_KEYS)
REQUIRED = {
    "READ": ("path",),
    "WRITE": ("path", "content"),
    "GREP": ("pattern",),
    "DIFF": (),
    "STATUS": (),
    "EXECUTE": ("command",),
    "AST_PARSE": ("path",),
    "AST_QUERY": ("path", "pattern"),
    "SYMBOL_SUMMARY": (),
}
STR_KEYS = ("path", "content", "pattern", "target", "command")
INT_KEYS = ("line_start", "line_end", "max_results")
MAX_OPS = 32        # a batch is bounded; beyond this it is not a plan
MAX_STR = 65536     # control channel: no megabyte literals
MAX_INT = 1 << 40   # line numbers etc. stay sane

PLAN_GBNF = r'''# SwarmState plan grammar (spec v1) -- llama.cpp GBNF.
# Constrains generation: shape, op-type enum, strategy enum, value
# terminals.  Per-type REQUIRED fields are enforced by the strict
# validator (dependent fields are impractical in GBNF).
root      ::= ws "{" ws ops "," ws strategy ws "}"
ops       ::= "\"ops\"" ws ":" ws "[" ws op (ws "," ws op)* ws "]"
op        ::= "{" ws "\"type\"" ws ":" ws op_type (ws "," ws op_field)* ws "}"
op_type   ::= "\"READ\"" | "\"WRITE\"" | "\"GREP\"" | "\"DIFF\"" |
              "\"STATUS\"" | "\"EXECUTE\"" | "\"AST_PARSE\"" |
              "\"AST_QUERY\"" | "\"SYMBOL_SUMMARY\""
op_field  ::= "\"path\"" ws ":" ws string |
              "\"content\"" ws ":" ws string |
              "\"pattern\"" ws ":" ws string |
              "\"target\"" ws ":" ws string |
              "\"command\"" ws ":" ws string |
              "\"line_start\"" ws ":" ws number |
              "\"line_end\"" ws ":" ws number |
              "\"max_results\"" ws ":" ws number
strategy  ::= "\"strategy\"" ws ":" ws ("\"DELTA\"" | "\"TARGETED\"" |
              "\"FULL\"" | "\"SYMBOLIC\"")
string    ::= "\"" char* "\""
char      ::= [^"\\] | "\\" escape
escape    ::= ["\\/bfnrt]
number    ::= "-"? [0-9]+
ws        ::= [ \t\n]*
'''


def validate(plan: object) -> tuple[bool, list[str]]:
    """Strict structural gate.  Returns (ok, errors); errors are
    actionable (they name the exact op and field that failed)."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return False, ["plan is not a JSON object"]
    ops = plan.get("ops")
    if not isinstance(ops, list) or not ops:
        errors.append("plan.ops must be a non-empty list")
        ops = []
    if len(ops) > MAX_OPS:
        errors.append(f"plan.ops has {len(ops)} entries (max {MAX_OPS})")
    strategy = str(plan.get("strategy", "")).upper()
    if strategy and strategy not in VALID_STRATEGIES:
        errors.append(f"strategy '{plan.get('strategy')}' is invalid "
                      f"(want one of {', '.join(VALID_STRATEGIES)})")
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            errors.append(f"op[{i}] is not an object")
            continue
        otype = str(op.get("type", "")).upper()
        if otype not in VALID_TYPES:
            errors.append(f"op[{i}].type '{op.get('type')}' is unknown "
                          f"(want one of {', '.join(VALID_TYPES)})")
            continue
        for key in REQUIRED.get(otype, ()):
            val = op.get(key)
            if val is None or str(val).strip() == "":
                errors.append(f"op[{i}].{key} is required for {otype}")
        for key in STR_KEYS:
            val = op.get(key)
            if val is not None and not isinstance(val, str):
                errors.append(f"op[{i}].{key} must be a string, "
                              f"got {type(val).__name__}")
            elif isinstance(val, str) and len(val) > MAX_STR:
                errors.append(f"op[{i}].{key} exceeds {MAX_STR} chars")
        for key in INT_KEYS:
            val = op.get(key)
            if val is not None and not isinstance(val, int):
                errors.append(f"op[{i}].{key} must be an integer, "
                              f"got {type(val).__name__}")
            elif isinstance(val, int) and abs(val) > MAX_INT:
                errors.append(f"op[{i}].{key} out of range")
    return (not errors, errors)
