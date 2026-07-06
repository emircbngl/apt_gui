@echo off
title Thorlabs APT Stage Controller - Setup
echo ============================================================
echo   Thorlabs APT Stage Controller - Windows Setup
echo ============================================================
echo.

REM Check if Python exists
python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python found.
    goto :install_deps
)

python3 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python3 found.
    goto :install_deps
)

echo [!] Python not found. Downloading Python now...
echo.

REM Download Python installer
powershell -Command "& {Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%TEMP%\python_installer.exe'}"

if not exist "%TEMP%\python_installer.exe" (
    echo [ERROR] Could not download Python. Check your internet connection and retry.
    echo Or download it manually from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Installing Python (adding to PATH)...
"%TEMP%\python_installer.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1

echo.
echo [!] Python installed. Close this window and run INSTALL.bat AGAIN.
pause
exit /b 0

:install_deps
echo.
echo [2/3] Installing dependencies...
pip install pyserial PyQt5 pyinstaller ftd2xx 2>nul || pip3 install pyserial PyQt5 pyinstaller ftd2xx

echo.
echo [3/3] Building the EXE...
pyinstaller --onefile --windowed --name "ThorlabsAPT" ^
    --add-data "devices.py;." ^
    --add-data "gui.py;." ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import ftd2xx ^
    main.py

echo.
echo ============================================================
if exist "dist\ThorlabsAPT.exe" (
    echo [OK] Success! Executable: dist\ThorlabsAPT.exe
    echo You can copy this file anywhere you like.
) else (
    echo [!] EXE build failed. You can still run the app with:
    echo     python main.py
)
echo ============================================================
pause
