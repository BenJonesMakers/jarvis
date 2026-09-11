<#
dev.ps1 — start JARVIS's backend and frontend in one command.

Opens two new PowerShell windows (backend, frontend) so you can watch each
log and Ctrl+C either one independently, then opens Chrome at the frontend
once Vite is likely up. Mirrors the two-terminal steps in README.md/CLAUDE.md
exactly; this script only saves typing them.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$cert = Join-Path $root "cert.pem"
$key = Join-Path $root "key.pem"
if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
    Write-Warning "cert.pem/key.pem not found in $root — the frontend proxy needs HTTPS on the backend. See CLAUDE.md for the openssl command."
}

Write-Host "Starting backend (server.py) ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root'; python server.py --host 127.0.0.1"
)

Write-Host "Starting frontend (vite) ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\frontend'; npm run dev"
)

Start-Sleep -Seconds 3
Start-Process "http://localhost:5173"

Write-Host "Backend and frontend are launching in their own windows. Close either window (or Ctrl+C in it) to stop that half." -ForegroundColor Green
