@echo off
title Trad-Auto Live Trading Engine & Web Dashboard
echo ========================================================================
echo   Starting Trad-Auto Live Engine with Local Web Dashboard
echo   Dashboard will be available at: http://localhost:5000/dashboard
echo ========================================================================
".venv\Scripts\python.exe" -m trad_auto.main %*
pause
