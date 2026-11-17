@echo off
chcp 65001 >nul
echo 启动 ScholarAgent 后端与前端开发服务...
cd /d "%~dp0\.."
start "ScholarAgent Backend" cmd /k "cd /d %cd%\backend && ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
start "ScholarAgent Frontend" cmd /k "cd /d %cd%\frontend && npm.cmd run dev"
timeout /t 3 >nul
start "" http://localhost:5173
