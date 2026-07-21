@echo off
chcp 65001 >nul
title PageBridge

echo ========================================
echo   📖 PageBridge — 一键启动
echo ========================================
echo.

:: 检查虚拟环境
if not exist .venv (
    echo [X] 未找到虚拟环境，请先执行:
    echo     python -m venv .venv
    pause
    exit /b
)

:: 检查 .env
if not exist .env (
    echo [X] 未找到 .env 文件，请从 .env.example 复制并填入 API Key
    pause
    exit /b
)

echo [1/3] 激活 Python 虚拟环境...
call .venv\Scripts\activate.bat

echo [2/3] 启动后端 (端口 8000)...
start "AI-Reader-Backend" cmd /c "call .venv\Scripts\activate.bat && uvicorn backend.main:app --reload --port 8000"

:: 等后端先起来
timeout /t 3 /nobreak >nul

echo [3/3] 启动前端 (端口 5173)...
cd frontend
start "AI-Reader-Frontend" cmd /c "npm run dev"
cd ..

echo.
echo ========================================
echo   ✅ 启动完成！
echo.
echo   前端: http://localhost:5173
echo   后端: http://localhost:8000
echo   API 文档: http://localhost:8000/docs
echo.
echo   关闭窗口即为停止服务
echo ========================================
echo.
pause
