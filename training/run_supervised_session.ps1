# Supervised overnight pool + 5-min health checks in ONE console.
# Close this window or Ctrl+C -> kills pool, supervisor, node workers, all titanium.exe.
#
#   training/run_supervised_session.cmd   (opens this window)
#   powershell -ExecutionPolicy Bypass -File training/run_supervised_session.ps1

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$script:SupervisorProc = $null
$script:PollRunspace = $null
$script:PollHandle = $null
$script:CleaningUp = $false

function Stop-SupervisedSession {
    param([switch]$Quiet)
    if ($script:CleaningUp) { return }
    $script:CleaningUp = $true

    if (-not $Quiet) {
        Write-Host ""
        Write-Host "=== STOPPING SESSION ===" -ForegroundColor Yellow
    }

    if ($script:PollRunspace) {
        try { $script:PollRunspace.Stop() } catch {}
        try { $script:PollRunspace.Dispose() } catch {}
        $script:PollRunspace = $null
        $script:PollHandle = $null
    }

    if ($script:SupervisorProc -and -not $script:SupervisorProc.HasExited) {
        try { $script:SupervisorProc.Kill() } catch {}
        try { $script:SupervisorProc.WaitForExit(3000) } catch {}
    }
    $script:SupervisorProc = $null

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -match 'run_swiss_overnight|supervise\.py|overnight_batch|remote_game_worker|run_nnue_cycle|coordinator\.py'
            )
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    cmd /c "taskkill /F /IM titanium.exe /T >nul 2>nul"
    Remove-Item -Force "$Root/training/data/eval_batch.lock" -ErrorAction SilentlyContinue

    if (-not $Quiet) {
        Write-Host "=== All workers stopped ===" -ForegroundColor Green
    }
}

# Trap Ctrl+C and normal PowerShell exit.
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Stop-SupervisedSession -Quiet } | Out-Null
trap {
    Stop-SupervisedSession
    break
}

# Trap console window close (X button) on Windows.
if ($IsWindows -or $env:OS -match "Windows") {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ConsoleCloseHook {
    public delegate bool Handler(int sig);
    [DllImport("Kernel32")]
    public static extern bool SetConsoleCtrlHandler(Handler h, bool add);
}
"@ -ErrorAction SilentlyContinue | Out-Null
    if ([ConsoleCloseHook]) {
        $script:ConsoleHandler = [ConsoleCloseHook+Handler]{
            param([int]$sig)
            Stop-SupervisedSession -Quiet
            return $false
        }
        [void][ConsoleCloseHook]::SetConsoleCtrlHandler($script:ConsoleHandler, $true)
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  QUORIDOR SUPERVISED TRAINING" -ForegroundColor Cyan
Write-Host "  Ctrl+C or close window -> pool + supervisor + titanium all stop" -ForegroundColor Cyan
Write-Host "  supervisor.log | nnue_train.log  (status every ~30s below)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# Initial orphan cleanup (must not abort on empty taskkill).
$ErrorActionPreference = "SilentlyContinue"
cmd /c "taskkill /F /IM titanium.exe /T >nul 2>nul"
$ErrorActionPreference = "Stop"
Stop-SupervisedSession -Quiet

Write-Host "[1/4] Native rebuild..." -ForegroundColor Gray
$env:RUSTFLAGS = "-C target-cpu=native"
Push-Location "$Root/engine"
$ErrorActionPreference = "Continue"
$cargoOut = & cargo build --release -p titanium 2>&1
$cargoRc = $LASTEXITCODE
foreach ($line in $cargoOut) {
    $text = if ($line -is [System.Management.Automation.ErrorRecord]) { $line.ToString() } else { "$line" }
    if ($text -match '\berror\[|\berror:') {
        Write-Host $text -ForegroundColor Red
    } elseif ($text -match 'warning:') {
        Write-Host $text -ForegroundColor Yellow
    } else {
        Write-Host $text
    }
}
$ErrorActionPreference = "Stop"
Pop-Location
if ($cargoRc -ne 0) { throw "cargo build failed (exit $cargoRc)" }
Write-Host "  build OK" -ForegroundColor Green

Write-Host "[2/4] Engine stamp + parity (6/6 required)..." -ForegroundColor Gray
& python "$Root/training/engine_identity.py" --write
if ($LASTEXITCODE -ne 0) { throw "engine_identity failed" }
& python "$Root/training/parity_check.py"
if ($LASTEXITCODE -ne 0) { throw "parity_check failed" }

Write-Host "[3/4] Catch-up pending micro-trains..." -ForegroundColor Gray
& python "$Root/training/run_nnue_cycle.py" --catch-up

Write-Host "[4/4] Starting supervisor + overnight pool..." -ForegroundColor Gray

# Child process (same console tree) — killed explicitly in Stop-SupervisedSession.
$supervisorPsi = New-Object System.Diagnostics.ProcessStartInfo
$supervisorPsi.FileName = "python"
$supervisorPsi.Arguments = "-u `"$Root/training/supervise.py`" --interval 300 --parity-every 3"
$supervisorPsi.WorkingDirectory = $Root
$supervisorPsi.UseShellExecute = $false
$supervisorPsi.CreateNoWindow = $true
$script:SupervisorProc = [System.Diagnostics.Process]::Start($supervisorPsi)

$logPath = "$Root/training/data/supervisor.log"
$script:PollRunspace = [powershell]::Create().AddScript({
    param($LogPath)
    $last = ""
    while ($true) {
        Start-Sleep -Seconds 30
        if (Test-Path $LogPath) {
            $line = Get-Content $LogPath -Tail 1 -ErrorAction SilentlyContinue
            if ($line -and $line -ne $last) {
                $last = $line
                [Console]::WriteLine("[supervisor] $line")
            }
        }
    }
}).AddArgument($logPath)
$script:PollHandle = $script:PollRunspace.BeginInvoke()

try {
    & python -u "$Root/training/run_swiss_overnight.py"
}
finally {
    Stop-SupervisedSession
}
