@echo off
title Build RetailPOS EXE
color 0A
echo.
echo  =============================================
echo   RetailPOS  -  Building Standalone EXE
echo  =============================================
echo.

cd /d "%~dp0"

REM Find Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found on PATH.
    echo  Install Python from https://python.org then re-run this.
    pause
    exit /b 1
)

REM Make sure dependencies are installed
echo  Checking dependencies...
pip install flask werkzeug requests pyinstaller --quiet --user

REM Find pyinstaller (handles the case where it's installed in user Scripts)
set "PYINST="
for /f "delims=" %%i in ('python -c "import sys,os; [print(os.path.join(d,'pyinstaller.exe')) for d in [os.path.join(sys.prefix,'Scripts'), os.path.join(os.path.expanduser('~'),'AppData','Roaming','Python','Python%d%d' %% (sys.version_info.major,sys.version_info.minor),'Scripts')] if os.path.exists(os.path.join(d,'pyinstaller.exe'))]" 2^>nul') do set "PYINST=%%i"

if "%PYINST%"=="" (
    REM fallback: use python -m PyInstaller
    set "PYINST=python -m PyInstaller"
)

echo.
echo  Building... this takes 2-5 minutes, please wait.
echo.

%PYINST% RetailPOS.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo  =============================================
    echo   BUILD FAILED  -  see errors above
    echo  =============================================
    pause
    exit /b 1
)

REM Copy pos.db and config.py next to the EXE so users can find them
if exist "dist\RetailPOS" (
    if exist "pos.db"    copy /y "pos.db"    "dist\RetailPOS\pos.db"    >nul
    if exist "config.py" copy /y "config.py" "dist\RetailPOS\config.py" >nul
    if exist "sync.py"   copy /y "sync.py"   "dist\RetailPOS\sync.py"   >nul
    if exist "config.py" copy /y "config.py" "dist\RetailPOS\_internal\config.py" >nul
    if exist "sync.py"   copy /y "sync.py"   "dist\RetailPOS\_internal\sync.py"   >nul
    echo  Copied pos.db, config.py and sync.py into dist\RetailPOS\
)

echo.
echo  =============================================
echo   BUILD SUCCESSFUL
echo.
echo   Your application is in:
echo     dist\RetailPOS\RetailPOS.exe
echo.
echo   To deploy to another PC:
echo     Copy the ENTIRE  dist\RetailPOS\  folder.
echo     Double-click  RetailPOS.exe  to run.
echo     No Python needed on the target PC.
echo  =============================================
echo.
pause
