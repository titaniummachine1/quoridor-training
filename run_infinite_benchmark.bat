@echo off
:: Infinite Elo tracker: Titanium v15 vs ace-v13-ti-pure baseline.
:: Each game is saved and ingested into training/data/all_games.db automatically.
:: Check progress anytime:  training\data\STATUS.txt
:: Stop with Ctrl+C — totals are saved in training\data\manifest.json

setlocal
cd /d "%~dp0"
python training\run_infinite_benchmark.py %*
