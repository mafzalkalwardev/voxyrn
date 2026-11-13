@echo off
setlocal
cd /d "%~dp0"
python -m src.main --realtime --agent-name Ashley --company-name "Indus Transports LLC" --limit 1 %*
pause
