@echo off
:: Print current Elo ladder + matchup W/L to the terminal.
setlocal
cd /d "%~dp0"
python training\run_swiss_overnight.py --scoreboard %*
