@echo off
rem SwarmState node launcher - starts the mesh daemon and opens the web UI.
cd /d "%~dp0"
start "" "http://127.0.0.1:8080"
swarmstate-node.exe --name my-node --port 7700 --web 8080 --demo --hash-interval 10
