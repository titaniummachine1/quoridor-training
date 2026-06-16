# Push root training repo to GitHub (quoridor-training).
# Create the empty repo first: https://github.com/new → quoridor-training (no README).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$remote = "https://github.com/titaniummachine1/quoridor-training.git"
git remote set-url origin $remote

Write-Host "Probing $remote ..." -ForegroundColor Gray
git ls-remote origin HEAD 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: GitHub repo not found." -ForegroundColor Red
    Write-Host "Create it first: https://github.com/new" -ForegroundColor Yellow
    Write-Host "  Owner: titaniummachine1" -ForegroundColor Yellow
    Write-Host "  Name:  quoridor-training" -ForegroundColor Yellow
    Write-Host "  Private recommended; do NOT init with README." -ForegroundColor Yellow
    exit 1
}

$branch = (git branch --show-current)
if (-not $branch) { $branch = "master" }

Write-Host "Pushing $branch -> origin ..." -ForegroundColor Cyan
git push -u origin $branch
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: https://github.com/titaniummachine1/quoridor-training" -ForegroundColor Green
}
