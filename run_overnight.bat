@echo off
:: OVERNIGHT STABLE LOOP — search/BFS fixed; NNUE prior drifts slowly.
:: Promote: drift>2cp OR move>5% AND Elo ok. 7 ladder slots + background micro-train.
:: Log: training\data\nnue_train.log  |  python training\plateau_probe.py --report
:: No train: run_overnight.bat --no-train

setlocal
cd /d "%~dp0"
python training\run_swiss_overnight.py --parallel 7 %*
