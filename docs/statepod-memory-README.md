# StatePod Memory Layer

This directory contains the memory layer for StatePod and its extensions.

## Contents

### .grok/memory/
- `config.json` - Extension memory configuration
- `harness/` - Memory for harness components
- `graphify/` - Memory for graphify extension
- `integrations/` - Memory for integration plugins
- `research/` - Memory for research corpus
- `docs/` - Memory for documentation layer

### Documentation

- `docs/statepod-memory-layer-spec.md` - Memory layer specification

## Memory Types

1. **Prompt Notes** - Behavioral guidelines
2. **Memories** - Durable facts
3. **Skills** - Reusable procedures
4. **Subagents** - Delegation patterns

## Integration with Auto-Learn
The memory layer captures lessons via the auto-learn system.

## Files Created
1. `docs/statepod-memory-layer-spec.md`
2. `.grok/memory/config.json`
3. `.grok/memory/harness/openssl_task_memory.json`
4. `.grok/memory/harness/adversarial_review_memory.json`
5. `scripts/statepod-memory-init.py`
