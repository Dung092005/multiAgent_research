# Stop Dispute Desk API/FE windows manually; this stops Postgres container.
# Usage: .\scripts\stop-desk.ps1

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Stopping Postgres..." -ForegroundColor Cyan

$existing = docker ps -a --filter "name=olist_disputes_db" --format "{{.Names}}"
if ($existing -eq "olist_disputes_db") {
    docker stop olist_disputes_db | Out-Null
    Write-Host "Stopped olist_disputes_db"
} else {
    docker compose stop postgres
    Write-Host "Stopped docker compose postgres"
}

Write-Host "Close the API and Frontend PowerShell windows to stop BE/FE."
