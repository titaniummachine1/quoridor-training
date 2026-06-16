@echo off
:: Quoridor strength test — Titanium v15 (current) vs ace-v13-ti-pure (JS v13 baseline)
:: 112 games, 5s/move, 4 concurrent. Games auto-saved + ingested for NNUE training.
:: Progress: training\data\STATUS.txt  (cumulative Elo, all runs combined)
:: Override any option, e.g.:
::   run_tests.bat --time 2
::   run_tests.bat --games 32

setlocal
set SCRIPT=%~dp0site\self_match.js
set GAMES=%~dp0training\data\v15_vs_ti_pure.games
set DEFAULTS=--games 112 --time 5 --concurrency 4 --engine-a titanium-v15 --engine-b ace-v13-ti-pure --save-games "%GAMES%" --source-tag v15-vs-ti-pure

node "%SCRIPT%" %DEFAULTS% %*
