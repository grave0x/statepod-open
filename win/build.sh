#!/usr/bin/env bash
# build.sh — cross-compile the Windows node (statepod-node.exe +
# statepod-kernel.dll) with mingw-w64, plus a Linux test binary.
# Reuses the shared C mesh base (libmesh) — no second mesh.
set -euo pipefail
cd "$(dirname "$0")/.."
MESH=${MESH:-"$HOME/src/mesh"}  # shared C mesh base checkout (see NOTICE)
CCW=x86_64-w64-mingw32-gcc
mkdir -p win/build

# embed the vendored QR library (MIT) for the UI's /qr.js endpoint
python3 - <<'PY'
data = open("win/qrcodegen.js", "rb").read()
with open("win/build/qrcodegen_data.h", "w") as f:
    f.write("/* generated from win/qrcodegen.js (MIT, kazuhikoarase/qrcode-generator) */\n")
    f.write("static const unsigned char qrlib_js[] = {\n")
    for i in range(0, len(data), 20):
        f.write("  " + ",".join("0x%02x" % b for b in data[i:i+20]) + ",\n")
    f.write("};\n")
    f.write("#define QR_LIB_LEN %d\n" % len(data))
PY

# 1) shared C mesh base for Windows (static: the zip ships exe + kernel dll)
"$CCW" -O2 -static -c "$MESH/src/mesh.c" -o win/build/mesh_win.o

# 2) precompiled kernel DLL (kernel.c + Windows shims + tree-sitter stubs)
"$CCW" -shared -static -O2 -Iwin/stubs -include win/compat/win_compat.h \
       kernel.c -o win/build/statepod-kernel.dll \
       -Wl,--out-implib,win/build/libkernel_win.a

# 3) node exe (links the kernel import lib + static mesh)
"$CCW" -O2 -static -I. -I. -Iwin -I"$MESH/include" -I"$MESH/src" \
       -Iwin/build win/statepod-node.c win/build/mesh_win.o \
       win/build/libkernel_win.a \
       -lws2_32 -o win/build/statepod-node.exe

# 4) Linux test binary (same node, real tree-sitter kernel)
if pkg-config --exists tree-sitter tree-sitter-c; then
  gcc -O2 -I. -I. -Iwin -I"$MESH/include" -I"$MESH/src" \
      -Iwin/build win/statepod-node.c "$MESH/src/mesh.c" kernel.c \
      $(pkg-config --cflags --libs tree-sitter tree-sitter-c) \
      -o win/build/statepod-node-linux
  echo "linux test binary: win/build/statepod-node-linux"
else
  echo "WARN: tree-sitter not found — skipping Linux test binary"
fi

# 5) Linux node with EMBEDDED inference (llama.cpp system runtime).
#    Default provider; falls back to Ollama when the model is absent.
if pkg-config --exists llama; then
  gcc -O2 -DSS_EMBED_INFER -I. -I. -Iwin -Iinfer -I"$MESH/include" -I"$MESH/src" \
      -Iwin/build win/statepod-node.c infer/llama_infer.c \
      "$MESH/src/mesh.c" kernel.c \
      $(pkg-config --cflags --libs tree-sitter tree-sitter-c) \
      $(pkg-config --libs llama) \
      -o win/build/statepod-node-embed
  echo "embedded node: win/build/statepod-node-embed"
else
  echo "WARN: llama not found — skipping embedded node"
fi
ls -la win/build/statepod-node.exe win/build/statepod-kernel.dll

# 6) Windows EXECUTE acceptance test (wine; real PowerShell not needed)
#    exec_win_test.exe drives the kernel DLL's EXECUTE path against a
#    powershell.exe shim (win/fake_powershell.c) that echoes the command
#    line: allowlist + command construction + CreateProcess + pipe capture.
#    Run:  PATH="win/build/fakeps:$PATH" wine win/build/exec_win_test.exe \
#          "Z:\path\to\test\root"
mkdir -p win/build/fakeps
"$CCW" -O2 win/fake_powershell.c -o win/build/fakeps/powershell.exe
"$CCW" -O2 -I. win/exec_win_test.c win/build/libkernel_win.a \
       -o win/build/exec_win_test.exe
cp win/build/fakeps/powershell.exe win/build/powershell.exe
