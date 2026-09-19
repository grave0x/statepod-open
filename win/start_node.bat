@echo off
rem StatePod node launcher - starts the mesh daemon and opens the web UI.
cd /d "%~dp0"
start "" "http://127.0.0.1:8080"
statepod-node.exe --name my-node --port 7700 --web 8080 --demo --hash-interval 10
