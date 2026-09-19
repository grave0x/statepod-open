# Todo Invariants Checklist

> Lint-friendly format. Used by `sp todo check`.

## Layer / parent invariants

- [ ] I1. Every Task has exactly one Story parent
- [ ] I2. Every Story has exactly one Epic parent
- [ ] I3. Every Epic has exactly one Vision parent
- [ ] I4. Every Vision has zero parents (no orphans)
- [ ] I5. Tasks never point to Epic or Vision directly
- [ ] I6. Epics never point to Vision directly without an intermediate

## Field invariants

- [ ] F1. `title` is non-empty and ≤ 80 chars
- [ ] F2. `description` is non-empty and ≥ 20 chars
- [ ] F3. `priority` is one of `P0 | P1 | P2 | P3`
- [ ] F4. `layer` is one of `vision | epic | story | task`
- [ ] F5. `status` is one of `todo | in_progress | done | blocked | cancelled`

## Story-specific

- [ ] S1. Story body contains `## Acceptance` or `## Done` section
- [ ] S2. Story has at least one `Task` child
- [ ] S3. Story `description` includes the deliverable

## Epic-specific

- [ ] E1. Epic has at least one `Story` child
- [ ] E2. Epic `description` includes the scope boundary

## Vision-specific

- [ ] V1. Vision has at least one `Epic` child
- [ ] V2. Vision `description` is 1-3 lines (the why)

## Cycle / graph

- [ ] G1. No cycles in the dependency graph
- [ ] G2. No Tasks reachable from more than one Vision
- [ ] G3. No Story without at least one Task child (orphans)

## Hook (when invariant fails)

- Print the failed rule (e.g. `I1: T-0011 missing Story parent`)
- Refuse to mutate
- Suggest the corrective action

```bash
sp todo check                       # all tasks
sp todo check --task T-0011         # one task
sp todo check --strict              # treat warnings as errors
```
