# StatePod Memory Layer Specification

## Overview
This document defines the memory layer for the StatePod project and its extensions. The memory layer provides persistent state, learned behaviors, and historical data across all StatePod components.

## Memory Structure

### 1. Global StatePod Memory
- Location: `.grok/memory/` directory
- Purpose: Project-wide learned facts and behaviors
- Persistence: Across all sessions and components

### 2. Component-Specific Memory Layers
- **Grok**: `.grok/memory/` (main config, task history, learned fixtures)
- **Harness**: `.grok/memory/harness/` (harness-specific knowledge)
- **Graphify**: `.grok/memory/graphify/` (graph understanding cache)
- **Integrations**: `.grok/memory/integrations/` (plugin memory)
- **Research**: `.grok/memory/research/` (corpus and experimental findings)
- **Docs**: `.grok/memory/docs/` (documentation patterns)

### 3. Extension Memory Models

#### Grok Extension Memory
- Session patterns (task history, reviewers, findings)
- Task history (timestamps, outcomes, learned fixes)
- Learned fixtures (reusable patterns and configurations)

#### Harness Extension Memory
- OpenSSL certificate task knowledge
- Adversarial review patterns
- Build and test configurations

#### Graphify Extension Memory
- Node types and relationships
- Query and understanding caches
- Graph structure metadata

#### Research Memory
- Corpus structure and organization
- Research workflow patterns
- Experimental findings and insights

#### Docs Memory
- Documentation patterns
- Specification formats
- Cross-references between docs and implementations

## Memory Types

1. **Prompt Notes** - Behavioral guidelines and policies
2. **Memories** - Durable facts and learned knowledge
3. **Skills** - Reusable procedures and techniques
4. **Subagents** - Repeated delegation patterns

## Integration with Continual Harness
The memory layer integrates with auto-learn to capture session lessons and persist durable facts.
