@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
echo Dang kiem thu giong Edge + video tren BAN SAO du lieu (khong dung du lieu that)...
".venv\Scripts\python.exe" test_tts_pipeline.py %*
