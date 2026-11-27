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

:: ── 1. Install location ──────────────────────────────────────
set "INSTALL_DIR=C:\RetailPOS"
echo  Install location: %INSTALL_DIR%
echo.

:: ── 2. Copy app files ────────────────────────────────────────
echo  [1/4] Copying application files...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
xcopy /E /Y /I /Q "%~dp0*" "%INSTALL_DIR%\" >nul 2>&1
if errorlevel 1 (
  echo  ERROR: Could not copy files. Try running as Administrator.
  pause
  exit /b 1
)
echo        Done.

:: ── 3. Create .env ───────────────────────────────────────────
echo  [2/4] Setting up cloud credentials...
if not exist "%INSTALL_DIR%\.env" (
  echo SUPABASE_URL=https://your-project.supabase.co > "%INSTALL_DIR%\.env"
  echo SUPABASE_KEY=your-supabase-service-role-key >> "%INSTALL_DIR%\.env"
  echo DASHBOARD_PASSWORD=RetailPOS@2026 >> "%INSTALL_DIR%\.env"
  echo        .env created. Edit C:\RetailPOS\.env and paste your Supabase credentials.
) else (
  echo        .env already exists - skipped.
)

:: ── 4. Create desktop shortcut ───────────────────────────────
echo  [3/4] Creating desktop shortcut...
set "PS_TMP=%TEMP%\retailpos_shortcut.ps1"

echo $ws = New-Object -ComObject WScript.Shell > "%PS_TMP%"
echo $desktop = [Environment]::GetFolderPath('Desktop') >> "%PS_TMP%"
echo $sc = $ws.CreateShortcut("$desktop\RetailPOS.lnk") >> "%PS_TMP%"
echo $sc.TargetPath = 'C:\RetailPOS\RetailPOS.exe' >> "%PS_TMP%"
echo $sc.WorkingDirectory = 'C:\RetailPOS' >> "%PS_TMP%"
echo $sc.Description = 'RetailPOS Point of Sale System' >> "%PS_TMP%"
echo $sc.Save() >> "%PS_TMP%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP%"
del "%PS_TMP%" >nul 2>&1

:: Check any desktop location
if exist "%USERPROFILE%\Desktop\RetailPOS.lnk" (
  echo        Shortcut created on Desktop.
) else if exist "%USERPROFILE%\OneDrive\Desktop\RetailPOS.lnk" (
  echo        Shortcut created on Desktop.
) else (
  echo        Could not create shortcut - create it manually:
  echo        Right-click C:\RetailPOS\RetailPOS.exe -^> Send to -^> Desktop shortcut
)

:: ── 5. Windows startup ───────────────────────────────────────
echo  [4/4] Windows startup...
echo.
set /p STARTUP=  Add RetailPOS to Windows startup so it opens automatically? (Y/N): 
if /i "%STARTUP%"=="Y" (
  set "PS_TMP2=%TEMP%\retailpos_startup.ps1"

  echo $ws = New-Object -ComObject WScript.Shell > "%PS_TMP2%"
  echo $startup = [Environment]::GetFolderPath('Startup') >> "%PS_TMP2%"
  echo $sc = $ws.CreateShortcut("$startup\RetailPOS.lnk") >> "%PS_TMP2%"
  echo $sc.TargetPath = 'C:\RetailPOS\RetailPOS.exe' >> "%PS_TMP2%"
  echo $sc.WorkingDirectory = 'C:\RetailPOS' >> "%PS_TMP2%"
  echo $sc.Save() >> "%PS_TMP2%"

  powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TMP2%"
  del "%PS_TMP2%" >nul 2>&1
  echo        Done. RetailPOS will open automatically on Windows login.
) else (
  echo        Skipped.
)

:: ── Done ─────────────────────────────────────────────────────
echo.
echo  =========================================
echo   Setup complete!
echo.
echo   App installed to : C:\RetailPOS
echo   Desktop shortcut : RetailPOS
echo.
echo   NEXT STEP:
echo   Edit C:\RetailPOS\.env and replace
echo   the placeholder values with your real
echo   Supabase URL and Key.
echo.
echo   Owner dashboard  : https://pos-z7il.onrender.com
echo   Username         : admin
echo   Password         : RetailPOS@2026
echo  =========================================
echo.
pause
