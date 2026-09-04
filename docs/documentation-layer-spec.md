# SwarmState Documentation Layer — Specification (Phase-Agnostic)

> **Document status:** end-state design and building decisions. The v1
> subset implemented so far (SS_OP_SYMBOL_SUMMARY, C extraction, lazy
> content-hash cache, WRITE invalidation) is documented in `README.md`;
> everything else in this spec is the target.  

### 1. Motivation
SwarmState's kernel owns the repository state and controls what context the LLM sees. Currently, the orchestrator can request:
- `READ` (full file contents)
- `AST_PARSE` / `AST_QUERY` (syntactic structure)
- `GREP` (pattern matching)
- `DIFF`, `STATUS`

But for many reasoning tasks, the LLM doesn't need the full file or even full function bodies. It needs *semantic summaries*: what a function does, its signature, its dependencies, and how it relates to other code. Serving this pre-digested information can dramatically reduce token usage while maintaining (or improving) success rates, especially for small local models.

This specification defines a **Documentation Layer**—a kernel-owned, queryable index of code symbols and their semantic summaries—that acts as a compression layer between raw source and the LLM's context.

---

### 2. Design Principles
- **Kernel is the source of truth**: The documentation index is built, stored, and served by the C kernel. The harness and LLM never see the raw index directly.
- **Context containment**: The LLM receives only structured summaries, not full code, unless explicitly requested.
- **Incremental and diff-aware**: The index updates automatically when files change, using the kernel's existing diff/state machinery.
- **Registry learns**: The performance registry tracks whether using documentation summaries improves success rates per task signature, enabling adaptive context strategies.
- **Privacy**: All indexing and summarization happen locally. Generated summaries (if any) are stored locally; no code leaves the device unless the user opts into cloud summarization.

---

### 3. New Data Structures

#### 3.1 SymbolSummary
A structured, serializable summary of a code symbol (function, class, method, etc.).

```c
typedef struct {
    char* name;               // e.g., "parse_config"
    char* kind;               // "function", "class", "method", "struct", "enum"
    char* signature;          // e.g., "int parse_config(const char* path, Config* out)"
    char* docstring;          // first paragraph or extracted doc comment
    char** params;            // parameter names and types (optional)
    size_t param_count;
    char** returns;           // return type and description (optional)
    size_t return_count;
    char** dependencies;      // symbols this symbol calls or references
    size_t dependency_count;
    char** callers;           // symbols that call this symbol (if known)
    size_t caller_count;
    char* file_path;          // repo-relative path
    uint64_t line_start;      // line number where the symbol begins
    uint64_t line_end;        // line number where it ends
    char* summary;            // one-sentence semantic summary (human-written or generated)
    uint64_t last_modified;   // timestamp (wall-clock) of last change to this symbol
    uint64_t content_hash;    // hash of the source span, for staleness checks
} SymbolSummary;
```

#### 3.2 SymbolIndex
An in-memory and persisted index mapping `(file_path, symbol_name)` → `SymbolSummary`. Stored as a binary file in the repo state directory (e.g., `~/.local/state/swarmstate/symbols.bin`), with a JSONL fallback for debuggability.

```c
typedef struct {
    SymbolSummary** entries;   // dynamic array
    size_t count;
    uint64_t state_hash;       // hash of the repo state when index was built
    bool dirty;                // needs rebuild or incremental update
} SymbolIndex;
```

---

### 4. Kernel API Additions

#### 4.1 New Operation Type: `SS_OP_SYMBOL_SUMMARY`
Add to the `SS_OpType` enum:

```c
SS_OP_SYMBOL_SUMMARY,   // fetch symbol summaries for a path or query
```

**Input fields in `SS_Operation`:**
- `path` (optional): repo-relative file path to limit search. If `NULL`, search whole repo.
- `pattern` (optional): symbol name substring or glob. If `NULL`, return all symbols in scope.
- `target` (optional): if provided, lookup a specific symbol by exact name.

**Output:**
- `SS_Result.logs` contains one or more serialized `SymbolSummary` entries (JSON or plain text) suitable for inclusion in the LLM context.

#### 4.2 New Helper Function: `ss_build_symbol_index`
```c
int ss_build_symbol_index(RepoState* state, const char* path_filter);
```
Rebuilds or updates the symbol index. Uses tree-sitter to extract symbols, docstrings, and local call graphs. Should be callable both manually and automatically after writes/checkouts.

#### 4.3 New Helper Function: `ss_invalidate_symbols`
```c
int ss_invalidate_symbols(RepoState* state, const char** paths, size_t path_count);
```
Marks symbol summaries as stale when files change. Called internally after `WRITE` or `EXECUTE` (if git mutation is allowed).

---

### 5. Index Construction and Maintenance

#### 5.1 Extraction
The kernel uses its existing tree-sitter integration (already used for AST ops) to parse each source file and extract:
- Function/class/method definitions (via language-specific node types)
- Docstrings / leading comment blocks
- Type hints (where available)
- Call graph edges (local, best-effort)
- Caller relationships can be derived by scanning the whole repo once (or incrementally via `grep` and `AST_QUERY`).

#### 5.2 Initial Build
- On first activation (or when `state_hash` doesn't match), the kernel walks all files under the repo root, parsing those with supported language grammars.
- The resulting index is persisted and associated with the current `state_hash`.

#### 5.3 Incremental Updates
- After each `WRITE` op, the kernel identifies the modified file(s) and re-parses only those.
- It also updates callers/dependencies for symbols that may have changed signature.
- If a file is deleted or renamed, corresponding entries are removed/updated.

#### 5.4 Staleness
- Each `SymbolSummary` stores a `content_hash` of its source span.
- Before serving a summary, the kernel can optionally verify the hash against the current file (fast lookup) or rely on the fact that writes invalidate properly. For v1, rely on automatic invalidation after writes; a manual `ss_build_symbol_index` is available for out-of-band changes (e.g., user edits outside SwarmState).

---

### 6. Harness / Orchestrator Integration

#### 6.1 New Context Strategy Option: `SYMBOLIC`
Add to the `SS_ContextStrategy` enum:

```c
SS_CONTEXT_SYMBOLIC,    // serve symbol summaries instead of raw file content
```

The orchestrator can choose `SYMBOLIC` when:
- The task involves understanding, documenting, or refactoring code structure.
- The local model is small and benefits from compression.
- The registry indicates high success with `SYMBOLIC` for the current task signature.

#### 6.2 Plan Generation
When generating a plan, the LLM (or heuristic) may include operations like:
```json
{
  "type": "SYMBOL_SUMMARY",
  "path": "src/parser.c",
  "pattern": "parse_"
}
```
The kernel returns all matching summaries, formatted compactly.

#### 6.3 Context Budgeting
`SYMBOLIC` strategy falls between `DELTA` and `TARGETED` in token cost:
- `DELTA`: ~80–200 bytes (state summary only)
- `SYMBOLIC`: ~200–800 bytes (summaries of relevant symbols)
- `TARGETED`: ~500–1500 bytes (summaries + some raw file content)
- `FULL`: ~8 KB+

#### 6.4 Registry Integration
The performance registry now tracks success per signature and strategy. The orchestrator can learn:
- For task `add_function`, `SYMBOLIC` + `TARGETED` (only new function's relevant neighbors) works best.
- For `explain_code`, `SYMBOLIC` alone is usually sufficient.
Over time, the confidence gate can default to `SYMBOLIC` for appropriate signatures, reducing token use further.

---

### 7. Optional: Generated Summaries
For code without docstrings, the harness may use a local LLM (or cloud model if allowed) to generate one-sentence summaries in a low-priority background process. These summaries are stored in the index and served exactly like human-written ones. This is optional and must be controlled by user preference (privacy).

---

### 8. Security and Privacy
- The documentation index is stored locally, alongside the repo state.
- No source code leaves the device for indexing unless the user explicitly enables cloud summarization.
- Symbol summaries are derived from the repo; they do not expose more than the code itself.
- Access control follows the same path containment rules as other kernel operations (`ss_resolve` ensures paths stay inside root).

---

### 9. Performance Targets
- Building index for a 10k-line repo: < 500 ms (PC), < 2 s (mobile) using tree-sitter incremental parse.
- Incremental update for one file: < 20 ms.
- `SYMBOL_SUMMARY` query: < 1 ms (in-memory lookup).
- Index memory footprint: roughly 10–20% of source size (e.g., 100 KB index for 1 MB source).

---

### 10. Example

Assume the task is: *"Add a function to read the configuration and return a Config struct."*

**Without documentation layer (TARGETED):**
- LLM receives 1,500 bytes of raw file content from `config.c` and `config.h`.

**With documentation layer (SYMBOLIC):**
- Plan includes `SYMBOL_SUMMARY` for `config.h` and `config.c`, returning:
```text
[name: parse_config, kind: function, signature: int parse_config(const char* path, Config* out), docstring: "Parses a config file and fills the Config struct.", file_path: src/config.c, line: 42-88]
[name: Config, kind: struct, signature: struct Config { char* host; int port; }, file_path: src/config.h, line: 12-20]
```
- LLM receives ~250 bytes and can generate the new function without reading the whole file.

**Token savings:** ~80–90% compared to TARGETED for this task.

---

### 11. Testing
- Unit tests for symbol extraction from sample code (C, Python, JS).
- Index build/update/invalidate tests.
- `SYMBOL_SUMMARY` query correctness.
- Harness-level tests showing that the orchestrator can use `SYMBOLIC` strategy and that registry learns from it.
- Cross-compilation check for ARM64 to ensure tree-sitter integration remains portable.

---

### 12. Open Questions
- Should `SYMBOL_SUMMARY` support multi-language grammars out of the box (tree-sitter has many, but including all increases binary size)? For v1, support C, Python, JavaScript, Rust, Go.
- Should the index be versioned with the repo state hash, or independently? The spec assumes it's tied to `state_hash`, but a finer-grained invalidation might be better for large repos.
- How to handle macros, templates, or dynamic languages where symbol extraction is less reliable? Fallback to raw `READ` is always available.
- Can the documentation layer also serve *module-level* summaries (e.g., "this file handles networking")? That could be a future extension.

---

This specification can be implemented as a self-contained module within the existing kernel and harness, without requiring changes to the core philosophy. It enhances the system's ability to serve meaningful context to small models, further reducing token usage and improving the quality of plans.
