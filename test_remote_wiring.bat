@echo off
:: Probe Ka + Ishtar at all 4 presets each — verify WebSocket wiring + measure think times.
:: Saves training\data\remote_timing.json for fair-time bootstrap.

setlocal
cd /d "%~dp0"
node site\test_remote_wiring.js %*
