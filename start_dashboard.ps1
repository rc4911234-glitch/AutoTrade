# Trad-Auto Dashboard Launcher for PowerShell
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "  Starting Trad-Auto Live Engine with Local Web Dashboard" -ForegroundColor Green
Write-Host "  Dashboard URL: http://localhost:5000/dashboard" -ForegroundColor Yellow
Write-Host "========================================================================" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m trad_auto.main @args
