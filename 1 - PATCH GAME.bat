@echo off
rem ============================================================
rem  Darksiders 2 local server - one-time game patching
rem  Steps:
rem    1) mint_certs.py  - backup+clear cert-tools\out, mint a fresh
rem                        coherent cert set, verify, assemble server-set
rem    2) patch_game.py  - hash-verify + patch Client\debug\
rem                        Darksiders2.exe.original into Client\out\
rem  Self-elevates once.  Deploy outputs manually (see Docs).
rem ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"

echo [1/2] Minting fresh certificate set...
py mint_certs.py
if errorlevel 1 goto :err

echo.
echo [2/2] Patching Darksiders2.exe...
rem Expected SHA-256 of the pristine Darksiders2.exe.original
set DS2_EXE_SHA256=39FED14186C343FDF7F7D87B6FA779247A715347084D9E14A2708A0D7BF951B9
py patch_game.py
if errorlevel 1 goto :err

echo.
echo ============================================================
echo  Patching complete.
echo  Deploy:
echo    copy Client\out\Darksiders2.exe              -> game dir
echo    copy cert-tools\out\server-set\*             -> server\certs\
echo  Then run "2 - START SERVER.bat", then launch the game.
echo ============================================================
pause
exit /b 0

:err
echo.
echo ERROR: patching failed. See messages above.
pause
exit /b 1
