#Requires -Version 5.1
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  📖 AI 双语阅读器 — 一键启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查虚拟环境
if (-not (Test-Path ".venv")) {
    Write-Host "[X] 未找到虚拟环境，请先执行:" -ForegroundColor Red
    Write-Host "     python -m venv .venv"
    Read-Host "按回车退出"
    exit
}

# 检查 .env
if (-not (Test-Path ".env")) {
    Write-Host "[X] 未找到 .env 文件，请从 .env.example 复制并填入 API Key" -ForegroundColor Red
    Read-Host "按回车退出"
    exit
}

Write-Host "[1/3] 激活 Python 虚拟环境..."
$venvPath = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
. $venvPath

Write-Host "[2/3] 启动后端 (端口 8000)..."
$backendJob = Start-Job -Name "AI-Reader-Backend" -ScriptBlock {
    param($venv, $root)
    cd $root
    . $venv
    uvicorn backend.main:app --reload --port 8000
} -ArgumentList $venvPath, $PSScriptRoot

Start-Sleep -Seconds 3

Write-Host "[3/3] 启动前端 (端口 5173)..."
$frontendJob = Start-Job -Name "AI-Reader-Frontend" -ScriptBlock {
    param($root)
    cd "$root\frontend"
    npm run dev
} -ArgumentList $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  ✅ 启动完成！" -ForegroundColor Green
Write-Host ""
Write-Host "  前端: http://localhost:5173" -ForegroundColor Yellow
Write-Host "  后端: http://localhost:8000" -ForegroundColor Yellow
Write-Host "  API 文档: http://localhost:8000/docs" -ForegroundColor Yellow
Write-Host ""
Write-Host "  关闭此窗口 = 停止所有服务" -ForegroundColor Red
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "按 Ctrl+C 停止服务"

# 等待用户按 Ctrl+C
try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "正在停止服务..."
    Stop-Job $backendJob -ErrorAction SilentlyContinue
    Stop-Job $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob -ErrorAction SilentlyContinue
    Remove-Job $frontendJob -ErrorAction SilentlyContinue
}
