#!/usr/bin/env python3
"""todo_custom — custom fields for the 4-layer todo system (GitHub Issues parity).

Fields: labels, milestone, assignees, mentions, reactions, draft, estimate,
actual, due, closed_at, project, effort_score, impact_score, risk, weight,
checklist, links, watchers, priority_boost, swarm_tags.

Custom fields: drop a JSON catalog at ~/.swarmstate/todo-fields.json to add
project-specific fields (e.g. { "name": "team": "ftype": "text", "layers":
["epic"] }). The field is then accepted by `sw todo add --team ...` and
shown by `sw todo show`. Validation runs through the same FieldSpec as
built-in fields.
"""
from __future__ import annotations
import json, re, os, sys
from pathlib import Path
from datetime import datetime

# ── FieldSpec ───────────────────────────────────────────────────────────────
class FieldSpec:
    """Specification for one extension field."""
    def __init__(self, name, ftype, layers,
                 default=None, required=False,
                 choices=None, min_val=None, max_val=None,
                 validate_fn=None, doc=""):
        self.name = name
        self.ftype = ftype
        self.layers = layers
        self.default = default
        self.required = required
        self.choices = choices
        self.min_val = min_val
        self.max_val = max_val
        self.validate_fn = validate_fn
        self.doc = doc

    def applicable(self, layer: str) -> bool:
        return layer in self.layers

    def validate(self, value):
        """Returns (ok, error_msg)."""
        if value is None:
            return (True, None)
        try:
            if self.ftype == "bool":
                if not isinstance(value, bool):
                    return (False, f"{self.name}: expected bool, got {type(value).__name__}")
            elif self.ftype == "number":
                v = float(value)
                if self.min_val is not None and v < self.min_val:
                    return (False, f"{self.name}: {v} < min {self.min_val}")
                if self.max_val is not None and v > self.max_val:
                    return (False, f"{self.name}: {v} > max {self.max_val}")
            elif self.ftype == "select":
                if value not in (self.choices or []):
                    return (False, f"{self.name}: {value!r} not in {self.choices}")
            elif self.ftype in ("labels", "assignees", "mentions", "multiselect", "watchers"):
                if not isinstance(value, list):
                    return (False, f"{self.name}: expected list, got {type(value).__name__}")
                if self.choices:
                    bad = [v for v in value if v not in self.choices]
                    if bad:
                        return (False, f"{self.name}: invalid choices: {bad}")
            elif self.ftype in ("text", "duration"):
                if not isinstance(value, str):
                    return (False, f"{self.name}: expected str, got {type(value).__name__}")
            elif self.ftype == "date":
                if isinstance(value, str):
                    try:
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        return (False, f"{self.name}: invalid ISO date {value!r}")
            elif self.ftype in ("struct", "reactions", "checklist", "links"):
                if not isinstance(value, (dict, list)):
                    return (False, f"{self.name}: expected dict/list, got {type(value).__name__}")
            if self.validate_fn:
                return self.validate_fn(value)
        except Exception as e:
            return (False, f"{self.name}: validation crashed: {e}")
        return (True, None)


# ── Validators ──────────────────────────────────────────────────────────────
def _dur_validate(v):
    if not re.match(r"^\d+(?:\.\d+)?[mhd]$", str(v)):
        return (False, f"duration must match N(m|h|d), got {v!r}")
    return (True, None)

def _risk_validate(v):
    if v not in ("low", "medium", "high", "critical"):
        return (False, f"risk must be low|medium|high|critical, got {v!r}")
    return (True, None)

def _effort_validate(v):
    try:
        n = float(v)
    except Exception:
        return (False, f"effort_score must be 1..13, got {v!r}")
    if n not in (1, 2, 3, 5, 8, 13):
        return (False, f"effort_score must be 1, 2, 3, 5, 8, or 13, got {n}")
    return (True, None)

def _impact_validate(v):
    try:
        n = float(v)
    except Exception:
        return (False, f"impact_score must be 1..5, got {v!r}")
    if n < 1 or n > 5:
        return (False, f"impact_score must be 1..5, got {n}")
    return (True, None)

# ── Catalog ────────────────────────────────────────────────────────────────
EXT_FIELDS: dict[str, FieldSpec] = {
    "labels":       FieldSpec("labels", "labels", ["vision","epic","story","task"], default=[],
                              doc="GitHub-style labels (free-form strings)"),
    "milestone":    FieldSpec("milestone", "text", ["vision","epic","story","task"],
                              doc="Milestone name or ID (e.g. 'v1.0', 'Sprint 3')"),
    "assignees":    FieldSpec("assignees", "assignees", ["vision","epic","story","task"], default=[],
                              doc="Agent/user names assigned to this task"),
    "mentions":     FieldSpec("mentions", "mentions", ["vision","epic","story","task"], default=[],
                              doc="Cross-references to other task IDs"),
    "reactions":    FieldSpec("reactions", "reactions", ["vision","epic","story","task"], default={},
                              doc="Reactions: {thumbs_up: 1, heart: 2, ...}"),
    "draft":        FieldSpec("draft", "bool", ["vision","epic","story","task"], default=False,
                              doc="Mark as draft (hidden from auto-routing)"),
    "estimate":     FieldSpec("estimate", "duration", ["story","task"],
                              validate_fn=_dur_validate,
                              doc="Time estimate: 30m, 2h, 1d"),
    "actual":       FieldSpec("actual", "duration", ["story","task"],
                              validate_fn=_dur_validate,
                              doc="Actual time spent (set on complete)"),
    "due":          FieldSpec("due", "date", ["vision","epic","story","task"],
                              doc="Due date: ISO-8601 (YYYY-MM-DD)"),
    "closed_at":    FieldSpec("closed_at", "date", ["vision","epic","story","task"],
                              doc="Timestamp when task was completed"),
    "project":      FieldSpec("project", "text", ["epic","story"],
                              doc="Project name (e.g. 'swarmtui', 'kernel-v2')"),
    "effort_score": FieldSpec("effort_score", "number", ["story","task"],
                              min_val=1, max_val=13, validate_fn=_effort_validate,
                              doc="Planning poker Fibonacci score: 1, 2, 3, 5, 8, 13"),
    "impact_score": FieldSpec("impact_score", "number", ["epic","story"],
                              min_val=1, max_val=5, validate_fn=_impact_validate,
                              doc="Impact: 1=trivial, 5=critical"),
    "risk":         FieldSpec("risk", "select", ["vision","epic","story","task"],
                              default="low", choices=["low","medium","high","critical"],
                              validate_fn=_risk_validate,
                              doc="Risk: low|medium|high|critical"),
    "weight":       FieldSpec("weight", "number", ["vision","epic","story","task"],
                              default=1.0, min_val=0.0, max_val=100.0,
                              doc="Scheduling weight (float, default 1.0)"),
    "checklist":    FieldSpec("checklist", "checklist", ["vision","epic","story","task"], default=[],
                              doc="Inline checklist: [{text, done}, ...]"),
    "links":        FieldSpec("links", "links", ["vision","epic","story","task"], default=[],
                              doc="External refs: [{rel, url}, ...]"),
    "watchers":     FieldSpec("watchers", "assignees", ["vision","epic","story","task"], default=[],
                              doc="Users watching for updates"),
    "priority_boost": FieldSpec("priority_boost", "number", ["vision","epic","story","task"],
                                default=0, min_val=-10, max_val=10,
                                doc="Modifier added to base priority (-10..+10)"),
    "swarm_tags":   FieldSpec("swarm_tags", "labels", ["vision","epic","story","task"], default=[],
                              doc="Swarm tags: networked, offline, mesh, governance, security"),
}

# ── Catalog resolution ────────────────────────────────────────────────────
def _catalog_paths():
    src = Path(__file__).resolve().parent.parent / "scripts" / "sw-todo-fields.json"
    user = Path.home() / ".swarmstate" / "todo-fields.json"
    paths = [src]
    if user.exists():
        paths.insert(0, user)
    return paths

def load_catalog():
    """Return merged catalog (defaults + user overrides)."""
    merged = {k: v for k, v in EXT_FIELDS.items()}
    for path in _catalog_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            for name, spec in data.items():
                if name.startswith("_") or not isinstance(spec, dict):
                    continue  # skip _meta and non-spec entries
                if name in merged:
                    # patch defaults on existing
                    for k, v in spec.items():
                        if k == "validate_fn": continue
                        setattr(merged[name], k, v)
                else:
                    merged[name] = FieldSpec(
                        name=name,
                        ftype=spec.get("ftype", "text"),
                        layers=spec.get("layers", ["task"]),
                        default=spec.get("default"),
                        required=spec.get("required", False),
                        choices=spec.get("choices"),
                        min_val=spec.get("min_val"),
                        max_val=spec.get("max_val"),
                        doc=spec.get("doc", ""),
                    )
        except Exception as e:
            print(f"todo_custom: warn: cannot load {path}: {e}", file=sys.stderr)
    return merged

_CATALOG = None
def get_catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = load_catalog()
    return _CATALOG

# ── Public API ──────────────────────────────────────────────────────────────
def validate_value(name, value):
    cat = get_catalog()
    if name not in cat:
        return (True, None)
    return cat[name].validate(value)

def validate_all(fields):
    errors = []
    for name, value in fields.items():
        ok, err = validate_value(name, value)
        if not ok:
            errors.append(err)
    return errors

def layer_applicable(name, layer):
    cat = get_catalog()
    if name not in cat:
        return True
    return cat[name].applicable(layer)

# ── Flag parsing ──────────────────────────────────────────────────────────
def parse_flags(flags: dict) -> dict:
    """Translate raw --flag=value dict into field values.

    Recognises: labels/assignees/mentions/watchers/swarm_tags as comma-list,
    estimate/actual as duration string, due/closed_at as ISO date,
    risk/milestone/project as text, effort/impact as number, draft as bool,
    links/checklist as struct.
    """
    out = {}
    for key, raw in flags.items():
        if raw is False or raw is None:
            continue
        cat = get_catalog()
        spec = cat.get(key)
        if spec is None:
            continue
        ft = spec.ftype
        try:
            if ft in ("labels", "assignees", "mentions", "multiselect", "watchers"):
                if isinstance(raw, list):
                    out[key] = [str(x) for x in raw]
                elif isinstance(raw, str):
                    out[key] = [s.strip() for s in raw.split(",") if s.strip()]
                else:
                    out[key] = [str(raw)]
            elif ft == "text" or ft == "select":
                out[key] = str(raw) if raw else spec.default
            elif ft == "duration":
                out[key] = str(raw)
            elif ft == "date":
                out[key] = str(raw)
            elif ft == "number":
                if isinstance(raw, (int, float)):
                    out[key] = float(raw)
                else:
                    out[key] = float(str(raw))
            elif ft == "bool":
                if isinstance(raw, bool):
                    out[key] = raw
                else:
                    out[key] = str(raw).lower() in ("1", "true", "yes", "y")
            elif ft in ("struct", "reactions", "checklist", "links"):
                # --link rel=url or --link 'rel=url' (string)
                if isinstance(raw, str) and "=" in raw and ft == "links":
                    rel, url = raw.split("=", 1)
                    out[key] = [{"rel": rel.strip(), "url": url.strip()}]
                elif isinstance(raw, str) and "=" in raw and ft == "checklist":
                    out[key] = [{"text": raw, "done": False}]
                elif isinstance(raw, str):
                    out[key] = json.loads(raw)
                else:
                    out[key] = raw
        except (ValueError, TypeError) as e:
            out[key] = raw  # let validator catch
    return out

# ── Frontmatter ──────────────────────────────────────────────────────────
def parse_frontmatter(md_text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). All x_* fields are EXT fields.
    Standard fields (id, layer, status, etc.) stay at top level.
    """
    if not md_text.startswith("---"):
        return ({}, md_text)
    end = md_text.find("\n---", 3)
    if end == -1:
        return ({}, md_text)
    fm = md_text[3:end].strip()
    body = md_text[end+4:].lstrip("\n")
    fields = {}
    current_key = None
    current_list = None
    for line in fm.split("\n"):
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            current_key = key
            if key.startswith("x_") and key != "x_":
                # extension field
                ext_name = key[2:]
                if val.startswith("[") and val.endswith("]"):
                    fields[ext_name] = [s.strip().strip("'\"") for s in val[1:-1].split(",") if s.strip()]
                elif val.lower() in ("true", "false"):
                    fields[ext_name] = (val.lower() == "true")
                else:
                    fields[ext_name] = val
            elif val.startswith("[") and val.endswith("]"):
                current_list = []
                fields[key] = current_list
            else:
                fields[key] = val
        elif current_list is not None and line.strip().startswith("- "):
            current_list.append(line.strip()[2:].strip())
    return fields, body

def build_frontmatter(fields: dict) -> str:
    """Build YAML frontmatter from a fields dict. Extension fields are x_ prefixed."""
    lines = ["---"]
    std_order = ["id", "schema", "layer", "status", "priority", "category", "tags", "depends", "created", "updated"]
    for k in std_order:
        if k in fields:
            v = fields[k]
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                lines.append(f"{k}: {v}")
    # Extension fields with x_ prefix
    std_keys = set(std_order)
    for k, v in fields.items():
        if k in std_keys or k.startswith("x_"):
            continue
        xk = f"x_{k}"
        if isinstance(v, bool):
            lines.append(f"{xk}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{xk}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{xk}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"

# ── Main for testing ────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    cat = get_catalog()
    print(f"Loaded {len(cat)} extension fields:")
    for name, spec in sorted(cat.items()):
        print(f"  {name:18s} {spec.ftype:12s} layers={','.join(spec.layers):24s} {spec.doc}")
