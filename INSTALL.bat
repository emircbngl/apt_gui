@echo off
title Thorlabs APT Stage Controller - Kurulum
echo ============================================================
echo   Thorlabs APT Stage Controller - Windows Kurulum
echo ============================================================
echo.

REM Check if Python exists
python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python bulundu.
    goto :install_deps
)

python3 --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python3 bulundu.
    goto :install_deps
)

echo [!] Python bulunamadi. Simdi Python indiriliyor...
echo.

REM Download Python installer
powershell -Command "& {Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%TEMP%\python_installer.exe'}"

if not exist "%TEMP%\python_installer.exe" (
    echo [HATA] Python indirilemedi. Lutfen internete baglayin ve tekrar deneyin.
    echo Veya https://www.python.org/downloads/ adresinden elle indirin.
    pause
    exit /b 1
)

echo Python kuruluyor (PATH'e ekleniyor)...
"%TEMP%\python_installer.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1

echo.
echo [!] Python kuruldu. Bu pencereyi kapatip INSTALL.bat'i TEKRAR calistirin.
pause
exit /b 0

:install_deps
echo.
echo [2/3] Bagimliliklar kuruluyor...
pip install pyserial PyQt5 pyinstaller 2>nul || pip3 install pyserial PyQt5 pyinstaller

echo.
echo [3/3] EXE olusturuluyor...
pyinstaller --onefile --windowed --name "ThorlabsAPT" ^
    --add-data "devices.py;." ^
    --add-data "gui.py;." ^
    --hidden-import serial.tools.list_ports ^
    main.py

echo.
echo ============================================================
if exist "dist\ThorlabsAPT.exe" (
    echo [OK] Basarili! Calistirma dosyasi: dist\ThorlabsAPT.exe
    echo Bu dosyayi istediginiz yere kopyalayabilirsiniz.
) else (
    echo [!] EXE olusturulamadi. Asagidaki dosyalari kullanabilirsiniz:
    echo     python main.py
)
echo ============================================================
pause
