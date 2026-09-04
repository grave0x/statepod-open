"""Embedded inference handler - Python ctypes binding to libllama.so.

This module provides the Python interface to the C kernel's embedded
llama.cpp inference engine (llama_infer.h). It enables zero-hop
LLM calls with prefix KV caching and GBNF grammar enforcement.

Usage:
    from harness.infer import init, generate, is_available, get_plan_gbnf
    ctx = init("/path/to/model.gguf", n_ctx=512, n_gpu_layers="auto")
    result = generate(ctx, "Your prompt here", grammar=get_plan_gbnf())
    import json; plan = json.loads(result)
