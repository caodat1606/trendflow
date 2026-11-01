@echo off
rem TrendFlow - nhap dup de chay. Tat: dong cua so nay.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Chua cai dat. Hay chay install_windows.ps1 truoc.
  pause
  exit /b 1
)
for /f %%p in ('".venv\Scripts\python.exe" free_port.py') do set PORT=%%p
if "%PORT%"=="" (
  echo Khong con cong trong 8001-8020.
  pause
  exit /b 1
)
curl -s -m 2 http://127.0.0.1:11434/api/version >nul 2>&1 || echo [!] Ollama chua chay - mo ung dung Ollama de AI viet kich ban.
echo TrendFlow: http://127.0.0.1:%PORT%   (dong cua so nay de tat)
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%/discover"
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port %PORT%
pause
