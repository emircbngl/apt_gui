@echo off
title Thorlabs APT Stage Controller
if exist "dist\ThorlabsAPT.exe" (
    start "" "dist\ThorlabsAPT.exe"
) else (
    python main.py 2>nul || python3 main.py
)
