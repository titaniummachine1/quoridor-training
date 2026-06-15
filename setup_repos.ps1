# Distributes the four folders into their GitHub repos. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "C:\gitProjects\Quoridor best AI\setup_repos.ps1"
# Close VS Code / any git tools first.

$ErrorActionPreference = "Stop"
$root = "C:\gitProjects\Quoridor best AI"

# 0. Remove stale locks + any broken per-folder git dirs
Remove-Item -Force -ErrorAction SilentlyContinue "$root\.git\index.lock"
foreach ($d in @("engine", "site", "coordinator", "test-client")) {
    $lock = Join-Path $root "$d\.git\index.lock"
    Remove-Item -Force -ErrorAction SilentlyContinue $lock
}

$repos = @(
    @{
        dir   = "engine"
        url   = "https://github.com/titaniummachine1/titanium-quoridor.git"
        msg   = "Titanium engine: UCI protocol, WASM target, ACE v10 port"
        force = $true   # main currently holds old monorepo; replace with engine-only tree
    },
    @{
        dir   = "site"
        url   = "https://github.com/titaniummachine1/Titanium-Quoridor-Website.git"
        msg   = "Website, vendored JS engines (ACE v7/v8/v10), scrapes, benchmarks"
        force = $false
    },
    @{
        dir   = "coordinator"
        url   = "https://github.com/titaniummachine1/Titanium-Quoridor-Coordinator.git"
        msg   = "Cloudflare Worker coordinator: webhook, KV queue, SPRT, modes"
        force = $false
    },
    @{
        dir   = "test-client"
        url   = "https://github.com/titaniummachine1/titanium-quoridor-test-client.git"
        msg   = "Distributed test worker: engine acquisition + UCI match runner"
        force = $false
    }
)

foreach ($r in $repos) {
    $path = Join-Path $root $r.dir
    Write-Host "`n=== $($r.dir) -> $($r.url)" -ForegroundColor Cyan
    Push-Location $path

    if (-not (Test-Path ".git")) {
        git init -b main
    }

    $remotes = git remote 2>$null
    if ($remotes -contains "origin") { git remote remove origin }
    git remote add origin $r.url
    git add -A

    $status = git status --porcelain
    if ($status) {
        git commit -m $r.msg
    }
    else {
        $head = git rev-parse HEAD 2>$null
        if (-not $head) {
            throw "No changes and no commits in $($r.dir)"
        }
        Write-Host "  (no new changes; pushing existing commit $head)"
    }

    if ($r.force) {
        git push -u origin main --force
    }
    else {
        git push -u origin main
    }

    Pop-Location
}

# Engine as submodule of the website (keeps site clones small)
$sitePath = Join-Path $root "site"
Push-Location $sitePath
if (-not (Test-Path ".gitmodules")) {
    git submodule add https://github.com/titaniummachine1/titanium-quoridor.git engine
    git commit -m "Add titanium-quoridor engine submodule"
    git push
}
else {
    Write-Host "`n=== site/engine submodule already configured" -ForegroundColor Yellow
}
Pop-Location

Write-Host "`nDone. Repo map:" -ForegroundColor Green
Write-Host "  engine/        -> titanium-quoridor"
Write-Host "  site/          -> Titanium-Quoridor-Website (+ engine submodule)"
Write-Host "  coordinator/   -> Titanium-Quoridor-Coordinator"
Write-Host "  test-client/   -> titanium-quoridor-test-client"
Write-Host "`nOptional cleanup (obsolete monorepo git at workspace root):"
Write-Host "  Remove-Item -Recurse -Force '$root\.git'"
