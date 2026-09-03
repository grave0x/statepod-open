#!/bin/sh
# infer/build.sh — build the embedded inference smoke test (Milestone 1).
# LLVM path: default compiler is Clang (`CC=clang`); override with CC=gcc.
# Links the system llama.cpp runtime.  The flag is -lllama (three L's):
#   -l + llama  ->  libllama.so
set -e
cd "$(dirname "$0")/.."
CC="${CC:-clang}"
echo "infer/build.sh: CC=$CC"
"$CC" -O2 -Wall -Wextra -I. -Iinfer \
    infer/llama_infer.c infer/smoke_infer.c \
    -lllama -lm -o infer/smoke_infer
echo "built infer/smoke_infer"
