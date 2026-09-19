StatePod Node (Windows)

Double-click start_node.bat to launch the StatePod node and open its local web dashboard (http://127.0.0.1:8080).
The node runs fully offline - no Python or install needed - and if Ollama is running on this machine it is detected automatically and its models become inference providers on the mesh.

Details
- statepod-node.exe   the node (mesh daemon + web UI, self-contained)
- statepod-kernel.dll the precompiled StatePod kernel (v0.3.0)
- config/widgets.json  optional: widget layout per role (UI Kit spec)
- Join from another node: --peer HOST:PORT --allow my-node
- See win/build.sh for the cross-compile recipe (mingw-w64).
