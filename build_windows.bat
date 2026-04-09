@echo off
REM ============================================================
REM  Thorlabs APT Stage Controller - Windows Build Script
REM  Run this on a Windows machine with Python 3.10+ installed.
REM  It will create a standalone .exe in the dist/ folder.
REM ============================================================

echo [1/3] Installing dependencies...
pip install pyserial PyQt5 pyinstaller

echo [2/3] Building executable...
pyinstaller --onefile --windowed --name "ThorlabsAPT" --icon=NUL main.py

echo [3/3] Done!
echo.
echo Executable is at: dist\ThorlabsAPT.exe
echo Copy it to any Windows PC - no Python needed.
pause
