param(
    [int]$Threads = 4
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $Repo "training\data\overnight_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OpeningGate = Join-Path $LogDir "opening_exploration_enabled.json"
if (-not (Test-Path $OpeningGate)) {
    '{"enabled":true}' | Set-Content -Encoding ascii $OpeningGate
}
$OutLog = Join-Path $LogDir "local_game_pool.log"
$ErrLog = Join-Path $LogDir "local_game_pool_err.log"
$PidFile = Join-Path $LogDir "local_game_pool.pid"

if (Test-Path $PidFile) {
    $existingPid = [int](Get-Content $PidFile -Raw).Trim()
    $existingProc = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -EA SilentlyContinue
    if ($existingProc -and $existingProc.CommandLine -like "*local_game_pool.py*") {
        Write-Host "local_game_pool already running pid=$existingPid - skipping launch"
        exit 0
    }
}

$env:TITANIUM_GENERATION_ENGINE = "titanium-v16"
$env:RUSTFLAGS = "-C target-cpu=native"
$env:PYTHONPATH = Join-Path $Repo "training"
$env:PYTHONUNBUFFERED = "1"
# Opening exploration only fires for kind=selfplay matchups (use_opening
# requires `not mixed`). Without this, every non-prior game routes to the
# mixed-opponent pool and the local pool deterministically replays the same
# lines forever (new_pos=0). 0.375 prior => ~30% control overall once the
# separate Ka stream (~20%) is counted.
$env:STREAM_SELFPLAY_FRACTION = "1.0"
$env:STREAM_PRIOR_EPOCH_FRACTION = "0.375"

$py = (Get-Command python).Source
$script = Join-Path $Repo "training\local_game_pool.py"
$argList = @(
    "-u `"$script`"",
    "--threads $Threads --time 10 --nodes 400000",
    "--train-after-new-positions 0 --batch-games 999999",  # training owned by training_coordinator.py
    "--no-initial-epoch --no-parity --opening-exploration",
    "--explore-chance 0.35 --explore-start-ply 4 --explore-max-loss-cp 80",
    "--explore-candidate-count 14 --explore-top-n 4 --explore-temperature-cp 45",
    "--recent-replay-fraction 0.0 --recent-window-games 0"
) -join " "

$p = Start-Process -FilePath $py `
    -ArgumentList $argList `
    -WorkingDirectory $Repo `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -WindowStyle Hidden

$p.Id | Set-Content -Encoding ascii $PidFile
Write-Host "Detached local_game_pool pid=$($p.Id) threads=$Threads"
