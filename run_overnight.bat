@echo off
:: Random overnight: 4 different matchups in parallel, one progress bar each.
::
:: Local @5s:  every pair (v15, ti-pure, ace-v13, titanium)
:: Remote:      v15@5s vs Ka @ Alpha — all time presets (+ Ishtar when up)
::
:: Scoreboard prints after each batch. Also: scoreboard.bat
::
:: Preview matchups:  python training\run_swiss_overnight.py --list

setlocal
cd /d "%~dp0"
python training\run_swiss_overnight.py %*
