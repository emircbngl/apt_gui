@echo off
REM ============================================================
REM  Thorlabs APT Stage Controller - Windows Build Script
REM  Run on a Windows machine with Python 3.10+ installed.
REM  Produces a single-file .exe at dist\ThorlabsAPT.exe.
REM ============================================================

echo [1/3] Installing dependencies...
pip install pyserial PyQt5 pyinstaller ftd2xx || pip3 install pyserial PyQt5 pyinstaller ftd2xx

echo [2/3] Building the EXE...
pyinstaller --onefile --windowed --name "ThorlabsAPT" ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import ftd2xx ^
    main.py

echo [3/3] Done!
echo.
if exist "dist\ThorlabsAPT.exe" (
    echo [OK] Executable: dist\ThorlabsAPT.exe
    echo Copy it to any Windows PC - no Python needed.
) else (
    echo [!] EXE build failed. You can still run the app with "python main.py".
)
pause
