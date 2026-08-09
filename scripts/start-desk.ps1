# Start Dispute Desk stack: Postgres (if needed) + API + Frontend
# Usage:
#   .\scripts\start-desk.ps1
#   .\scripts\start-desk.ps1 -SkipDb
#   .\scripts\start-desk.ps1 -Ingest

param(
    [switch]$SkipDb,
    [switch]$Ingest
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Olist Dispute Desk ==" -ForegroundColor Cyan

if (-not $SkipDb) {
    $existing = docker ps -a --filter "name=olist_disputes_db" --format "{{.Names}}"
    if ($existing -eq "olist_disputes_db") {
        Write-Host "Starting existing container olist_disputes_db..."
        docker start olist_disputes_db | Out-Null
    } else {
        Write-Host "Starting Postgres via docker compose..."
        docker compose up -d postgres
    }
    Write-Host "Waiting for Postgres on :5432..."
    Start-Sleep -Seconds 3
}

if ($Ingest) {
    Write-Host "Importing Olist CSV into Postgres (one-time / refresh)..."
    & .\.venv\Scripts\python.exe -m scripts.import_olist_csv --data-dir data
}

Write-Host "Starting API on http://127.0.0.1:8000 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root'; .\.venv\Scripts\python.exe scripts\run_api.py"
)

Start-Sleep -Seconds 2

Write-Host "Starting Frontend on http://127.0.0.1:5173 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root\frontend'; npm.cmd run dev -- --host 127.0.0.1 --port 5173"
)

Write-Host ""
Write-Host "Opened 2 windows: API + Frontend." -ForegroundColor Green
Write-Host "UI:  http://127.0.0.1:5173"
Write-Host "API: http://127.0.0.1:8000/docs"
Write-Host "Stop DB later:  .\scripts\stop-desk.ps1"
