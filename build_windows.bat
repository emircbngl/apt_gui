@echo off
REM ============================================================
REM  Thorlabs APT Stage Controller - Windows Build Script
REM  Python 3.10+ kurulu bir Windows makinede calistirin.
REM  dist\ThorlabsAPT.exe adinda tek dosyalik bir .exe uretir.
REM ============================================================

echo [1/3] Bagimliliklar kuruluyor...
pip install pyserial PyQt5 pyinstaller ftd2xx || pip3 install pyserial PyQt5 pyinstaller ftd2xx

echo [2/3] EXE olusturuluyor...
pyinstaller --onefile --windowed --name "ThorlabsAPT" ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import ftd2xx ^
    main.py

echo [3/3] Bitti!
echo.
if exist "dist\ThorlabsAPT.exe" (
    echo [OK] Calistirma dosyasi: dist\ThorlabsAPT.exe
    echo Bu dosyayi Python kurulu olmayan herhangi bir Windows PC'ye kopyalayabilirsiniz.
) else (
    echo [!] EXE olusturulamadi. "python main.py" ile calistirabilirsiniz.
)
pause
