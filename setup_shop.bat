@echo off
:: ============================================================
::  RetailPOS — Shop PC Installer
::  Run this once on the shop PC to install the app.
::  Double-click it or run as Administrator for best results.
:: ============================================================
title RetailPOS Shop Installer

echo.
echo  =========================================
echo   RetailPOS — Shop PC Setup
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
  echo        .env created ^(fill in your Supabase credentials^).
) else (
  echo        .env already exists — skipped.
)

:: ── 4. Create desktop shortcut ────────────────────────────────
echo  [3/4] Creating desktop shortcut...
set "EXE=%INSTALL_DIR%\RetailPOS.exe"
set "SHORTCUT=%PUBLIC%\Desktop\RetailPOS.lnk"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut('%SHORTCUT%'); ^
   $sc.TargetPath = '%EXE%'; ^
   $sc.WorkingDirectory = '%INSTALL_DIR%'; ^
   $sc.Description = 'RetailPOS Point of Sale System'; ^
   $sc.Save()"

if exist "%SHORTCUT%" (
  echo        Shortcut created on Desktop.
) else (
  echo        Could not create shortcut — do it manually.
)

:: ── 5. Optional: add to Windows startup ──────────────────────
echo  [4/4] Windows startup...
echo.
set /p STARTUP="  Add RetailPOS to Windows startup so it opens automatically? (Y/N): "
if /i "%STARTUP%"=="Y" (
  set "STARTUP_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RetailPOS.lnk"
  powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $sc = $ws.CreateShortcut('%STARTUP_LINK%'); ^
     $sc.TargetPath = '%EXE%'; ^
     $sc.WorkingDirectory = '%INSTALL_DIR%'; ^
     $sc.Save()"
  echo        RetailPOS will now open automatically on Windows login.
) else (
  echo        Skipped.
)

:: ── Done ──────────────────────────────────────────────────────
echo.
echo  =========================================
echo   Setup complete!
echo.
echo   App installed to: %INSTALL_DIR%
echo   Desktop shortcut: RetailPOS
echo.
echo   Owner dashboard:
echo   https://pos-z7il.onrender.com
echo   Username: admin
echo   Password: RetailPOS@2026
echo  =========================================
echo.
pause
