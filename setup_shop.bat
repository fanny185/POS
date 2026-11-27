@echo off
:: ============================================================
::  RetailPOS - Shop PC Installer
::  Run this once on the shop PC to install the app.
::  Double-click it or run as Administrator for best results.
:: ============================================================
title RetailPOS Shop Installer

echo.
echo  =========================================
echo   RetailPOS - Shop PC Setup
echo  =========================================
echo.

:: ── 1. Decide install location ───────────────────────────────
set "INSTALL_DIR=C:\RetailPOS"
echo  Install location: %INSTALL_DIR%
echo.

:: ── 2. Copy app files ─────────────────────────────────────────
echo  [1/4] Copying application files...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
xcopy /E /Y /I /Q "%~dp0*" "%INSTALL_DIR%\" >nul 2>&1
if errorlevel 1 (
  echo  ERROR: Could not copy files. Try running as Administrator.
  pause
  exit /b 1
)
echo        Done.

:: ── 3. Create .env if it doesn't exist ───────────────────────
echo  [2/4] Setting up cloud credentials...
if not exist "%INSTALL_DIR%\.env" (
  (
    echo SUPABASE_URL=https://your-project.supabase.co
    echo SUPABASE_KEY=your-supabase-service-role-key
    echo DASHBOARD_PASSWORD=RetailPOS@2026
  ) > "%INSTALL_DIR%\.env"
  echo        .env created. Edit C:\RetailPOS\.env and paste your Supabase credentials.
) else (
  echo        .env already exists - skipped.
)

:: ── 4. Create desktop shortcut via temp ps1 file ─────────────
echo  [3/4] Creating desktop shortcut...
set "EXE=C:\RetailPOS\RetailPOS.exe"
set "SHORTCUT=%PUBLIC%\Desktop\RetailPOS.lnk"
set "PS_TMP=%TEMP%\make_shortcut.ps1"

(
  echo $ws = New-Object -ComObject WScript.Shell
  echo $sc = $ws.CreateShortcut('%SHORTCUT%')
  echo $sc.TargetPath = '%EXE%'
  echo $sc.WorkingDirectory = 'C:\RetailPOS'
  echo $sc.Description = 'RetailPOS Point of Sale System'
  echo $sc.Save()
) > "%PS_TMP%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP%"
del "%PS_TMP%" >nul 2>&1

if exist "%SHORTCUT%" (
  echo        Shortcut created on Desktop.
) else (
  echo        Could not create shortcut - create it manually.
  echo        Right-click C:\RetailPOS\RetailPOS.exe -^> Send to -^> Desktop shortcut.
)

:: ── 5. Optional: add to Windows startup ──────────────────────
echo  [4/4] Windows startup...
echo.
set /p STARTUP=  Add RetailPOS to Windows startup so it opens automatically? (Y/N): 
if /i "%STARTUP%"=="Y" (
  set "STARTUP_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RetailPOS.lnk"
  set "PS_TMP2=%TEMP%\make_startup.ps1"
  (
    echo $ws = New-Object -ComObject WScript.Shell
    echo $sc = $ws.CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RetailPOS.lnk')
    echo $sc.TargetPath = 'C:\RetailPOS\RetailPOS.exe'
    echo $sc.WorkingDirectory = 'C:\RetailPOS'
    echo $sc.Save()
  ) > "%PS_TMP2%"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP2%"
  del "%PS_TMP2%" >nul 2>&1
  echo        RetailPOS will now open automatically on Windows login.
) else (
  echo        Skipped.
)

:: ── Done ──────────────────────────────────────────────────────
echo.
echo  =========================================
echo   Setup complete!
echo.
echo   App installed to: C:\RetailPOS
echo   Desktop shortcut: RetailPOS
echo.
echo   IMPORTANT: Edit C:\RetailPOS\.env and
echo   fill in your real Supabase credentials.
echo.
echo   Owner dashboard:
echo   https://pos-z7il.onrender.com
echo   Username: admin
echo   Password: RetailPOS@2026
echo  =========================================
echo.
pause
