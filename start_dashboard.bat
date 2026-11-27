@echo off
title RetailPOS Owner Dashboard
color 0A

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not on PATH.
    pause & exit /b 1
)

pip show flask >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies...
    pip install -r requirements.txt
)

REM Open browser automatically after a short delay
start "" /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

echo.
echo  ============================================
echo   RetailPOS  -  Owner Dashboard
echo  ============================================
echo.
echo  This computer:   http://localhost:5000
echo.

REM Get local Wi-Fi IP and display it
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "169\." ^| findstr /v "172\." ^| findstr /v "192\.168\.137"') do (
    set IP=%%a
)
if defined IP (
    set IP=%IP: =%
    echo  Phone / Tablet:  http://%IP%:5000
    echo.
    echo  Open that address on any device on the same Wi-Fi.
) else (
    echo  Connect to Wi-Fi to get a network address for phones/tablets.
)

echo.
echo  ============================================
echo  Press Ctrl+C to stop the dashboard.
echo  ============================================
echo.

python dashboard\app.py

pause
