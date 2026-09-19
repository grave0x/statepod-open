#!/usr/bin/env python3
"""
StatePod Memory Layer Initialization Script
"""
import os
import json
from datetime import datetime

MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.grok', 'memory')
MEMORY_DIR = os.path.normpath(MEMORY_DIR)


def ensure_memory_structure():
    """Ensure all memory layer structures exist."""
    for subdir in ['harness', 'graphify', 'integrations', 'research', 'docs']:
        os.makedirs(os.path.join(MEMORY_DIR, subdir), exist_ok=True)
    print(f"Memory directory structure ensured at {MEMORY_DIR}")


def save_memory(extension, key, value, scope="global"):
    """Save memory for a specific extension."""
    ext_dir = os.path.join(MEMORY_DIR, extension)
    os.makedirs(ext_dir, exist_ok=True)
    memory_file = os.path.join(ext_dir, f"{scope}.json")
    memory = {}
    if os.path.exists(memory_file):
        with open(memory_file, 'r') as f:
            memory = json.load(f)
    memory[key] = {"value": value, "timestamp": datetime.now().isoformat(), "scope": scope}
    with open(memory_file, 'w') as f:
        json.dump(memory, f, indent=2)
    print(f"Memory saved: {extension}/{scope}/{key}")


def load_memory(extension, key=None, scope="global"):
    """Load memory for a specific extension."""
    memory_file = os.path.join(MEMORY_DIR, extension, f"{scope}.json")
    if not os.path.exists(memory_file):
        return None
    with open(memory_file, 'r') as f:
        memory = json.load(f)
    return memory[key] if key else memory


def list_memory(extension, scope="global"):
    """List all memory keys for an extension."""
    memory = load_memory(extension, scope=scope)
    return list(memory.keys()) if memory else []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="StatePod Memory Layer Manager")
    parser.add_argument("action", choices=["init", "save", "load", "list"], help="Action to perform")
    parser.add_argument("--extension", required=True, help="Extension name")
    parser.add_argument("--key", help="Memory key")
    parser.add_argument("--value", help="Memory value (for save)")
    parser.add_argument("--scope", default="global", help="Memory scope (global/session)")
    args = parser.parse_args()
    if args.action == "init":
        ensure_memory_structure()
    elif args.action == "save":
        if not args.key:
            print("--key is required for save action")
        else:
            save_memory(args.extension, args.key, args.value, args.scope)
    elif args.action == "load":
        result = load_memory(args.extension, args.key, args.scope)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("No memory found")
    elif args.action == "list":
        keys = list_memory(args.extension, args.scope)
        print("Memory keys:")
        for key in keys:
            print(f"  - {key}")
