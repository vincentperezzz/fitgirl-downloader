@echo off
title Build FitGirl Downloader Executable
cd /d "%~dp0"

echo ===================================================
echo     FitGirl Downloader & Monitor - EXE Builder
echo ===================================================
echo.

echo [1/3] Ensuring PyInstaller is installed...
python -m pip install pyinstaller -q

echo.
echo [2/3] Building standalone Windows executable...
python -m PyInstaller --noconsole --onefile --clean --name "FitGirlDownloader" --icon "assets\icon.ico" --add-data "assets;assets" gui.py

echo.
echo [3/3] Done!
echo ===================================================
echo The standalone EXE has been generated at:
echo   dist\FitGirlDownloader.exe
echo ===================================================
echo.
pause
