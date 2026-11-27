@echo off
title RetailPOS
echo.
echo  ============================================
echo   RetailPOS  -  Point of Sale
echo  ============================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not on PATH.
    pause
    exit /b 1
)

python main.py

if errorlevel 1 (
    echo.
    echo  POS exited with an error.  See above for details.
    pause
)
