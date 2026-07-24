#Requires -Version 5.1
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PageBridge - Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".venv")) {
    Write-Host "[ERROR] Virtual env not found. Run: python -m venv .venv" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit
}

if (-not (Test-Path ".env")) {
    Write-Host "[ERROR] .env not found. Copy from .env.example and set your API key" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit
}

Write-Host "[1/3] Starting backend (port 8000)..."
$venvPath = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
$backendCmd = "cd '$PSScriptRoot'; . '$venvPath'; uvicorn backend.main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd -WindowStyle Normal

Start-Sleep -Seconds 4

Write-Host "[2/3] Starting frontend (port 5173)..."
$frontendCmd = "cd '$PSScriptRoot\frontend'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd -WindowStyle Normal

Write-Host "[3/3] Opening Chrome..."
Start-Sleep -Seconds 2
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (Test-Path $chrome) {
    Start-Process $chrome "http://localhost:5173"
} else {
    Start-Process "http://localhost:5173"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All services started!" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend : http://localhost:5173" -ForegroundColor Yellow
Write-Host "  Backend  : http://localhost:8000" -ForegroundColor Yellow
Write-Host "  API Docs : http://localhost:8000/docs" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Note: For translation and parsing, also start the worker:" -ForegroundColor Yellow
Write-Host "        In a third terminal: python -m backend.worker" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Close the service windows to stop" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Green

Start-Sleep -Seconds 2
