param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'SilentlyContinue'

$REPO       = "c:\gitProjects\Quoridor best AI"
$LOG        = "$REPO\training\data\overnight_logs\watchdog.log"
$LOCK       = "$REPO\training\data\overnight_logs\continuous_pool.lock.json"
$SSH_KEY    = "$env:USERPROFILE\.ssh\oracle_titanium.key"
$ORACLE_IP  = "92.5.77.92"
$TOKEN_FILE = "$env:LOCALAPPDATA\titanium-oracle-api-token"

function Write-Log {
    param([string]$Msg)
    $ts   = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    $line = "$ts  $Msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

function Get-ApiToken {
    if (Test-Path $TOKEN_FILE) {
        return (Get-Content $TOKEN_FILE -Raw -Encoding ascii).Trim()
    }
    try {
        $t = ssh -i $SSH_KEY -o ConnectTimeout=10 -o BatchMode=yes `
            "ubuntu@$ORACLE_IP" "sudo cat /var/lib/titanium-game-factory/api_token" 2>$null
        if ($t) {
            Set-Content -Path $TOKEN_FILE -Value $t -NoNewline -Encoding ascii
            return $t.Trim()
        }
    } catch { }
    return $null
}

function Test-TunnelUp {
    $tok = Get-ApiToken
    if (-not $tok) { return $false }
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" `
            -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 5 2>$null
        return ($null -ne $r)
    } catch { return $false }
}

function Get-TunnelPid {
    $p = Get-CimInstance Win32_Process 2>$null |
         Where-Object { $_.CommandLine -like "*oracle_titanium.key*" -and $_.CommandLine -like "*8765*" } |
         Select-Object -First 1
    if ($p) { return $p.ProcessId } else { return $null }
}

function Start-Tunnel {
    $tok = Get-ApiToken
    if (-not $tok) { Write-Log "WARN: no API token - tunnel skipped"; return }
    Write-Log "Starting SSH tunnel..."
    $sshArgs = @(
        "-i", $SSH_KEY,
        "-N", "-L", "8765:127.0.0.1:8765",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=6",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
        "ubuntu@$ORACLE_IP"
    )
    Start-Process -FilePath "ssh" -ArgumentList $sshArgs -WindowStyle Hidden
    Start-Sleep -Seconds 6
    if (Test-TunnelUp) {
        Write-Log "Tunnel UP"
    } else {
        Write-Log "WARN: tunnel started but API not reachable yet (will retry next cycle)"
    }
}

function Get-PoolPid {
    $p = Get-CimInstance Win32_Process 2>$null |
         Where-Object { $_.CommandLine -like "*local_game_pool*" -or $_.CommandLine -like "*continuous_pool*" } |
         Select-Object -First 1
    if ($p) { return $p.ProcessId } else { return $null }
}

function Start-Pool {
    $tok = Get-ApiToken
    if (-not $tok) { Write-Log "WARN: no API token - pool skipped"; return }
    Remove-Item $LOCK -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    $poolLog = Join-Path $REPO "training\data\overnight_logs\continuous_pool.log"
    Write-Log "Starting pool via start_overnight_pool.ps1 (log -> $poolLog)..."
    $ps1 = Join-Path $REPO "start_overnight_pool.ps1"
    $proc = Start-Process -FilePath "powershell" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ps1`"") `
        -WorkingDirectory $REPO `
        -WindowStyle Minimized `
        -PassThru
    Start-Sleep -Seconds 6
    $pid2 = Get-PoolPid
    if ($pid2) { Write-Log "Pool started pid=$pid2" } else { Write-Log "WARN: pool process not detected" }
}

# ---- main -------------------------------------------------------------------
Write-Log "=== Watchdog started - Ctrl+C to stop ==="

$firstRun = $true

while ($true) {
    # 1. Tunnel check
    $tUp = Test-TunnelUp
    if (-not $tUp) {
        $tpid = Get-TunnelPid
        if ($tpid) {
            Write-Log "Tunnel pid=$tpid alive but API unreachable - restarting"
            Stop-Process -Id $tpid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        Start-Tunnel
    }

    # 2. Pool check
    $ppid = Get-PoolPid
    if (-not $ppid) {
        Write-Log "Pool not running - starting"
        Start-Pool
    } elseif ($firstRun) {
        Write-Log "Pool already running pid=$ppid (watchdog attached)"
    }
    $firstRun = $false

    # 3. Heartbeat
    $ppid2   = Get-PoolPid
    $pStatus = if ($ppid2) { "pid=$ppid2" } else { "DEAD" }
    $tStatus = if (Test-TunnelUp) { "OK" } else { "DOWN" }
    Write-Log "heartbeat pool=$pStatus tunnel=$tStatus"

    Start-Sleep -Seconds 60
}
