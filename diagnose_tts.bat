@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
".venv\Scripts\python.exe" diagnose_tts.py
