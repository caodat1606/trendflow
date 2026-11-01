@echo off
rem TrendFlow launcher. Usage: trendflow [start^|stop^|status^|update^|backup^|logs^|restart]
setlocal
set "TF_ROOT=%~dp0.."
if not exist "%TF_ROOT%\.venv\Scripts\python.exe" (
  echo [TrendFlow] Chua cai dat. Chay: powershell -ExecutionPolicy Bypass -File "%TF_ROOT%\scripts\setup_windows.ps1"
  exit /b 1
)
if "%TRENDFLOW_DATA_DIR%"=="" set "TRENDFLOW_DATA_DIR=%USERPROFILE%\TrendFlowData"
set PYTHONUTF8=1
"%TF_ROOT%\.venv\Scripts\python.exe" "%TF_ROOT%\trendflow_cli.py" %*
exit /b %ERRORLEVEL%
