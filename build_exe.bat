@echo off
title Build FitGirl to IDM
cd /d "%~dp0"

echo ===================================================
echo     FitGirl to IDM - app and installer builder
echo ===================================================
echo.

echo [1/3] Ensuring PyInstaller is installed...
python -m pip install pyinstaller -q

echo.
echo [2/3] Building the app folder (onedir, no UPX: fewer antivirus false positives)...
python -m PyInstaller --noconsole --onedir --noupx --clean --noconfirm --name FitGirlToIDM ^
    --icon "assets\icon.ico" --add-data "assets;assets" --hidden-import idm_proxy gui.py
if errorlevel 1 goto :fail

echo.
echo [3/3] Building the installer with Inno Setup...
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup
    echo The app folder is still usable at dist\FitGirlToIDM\FitGirlToIDM.exe
    goto :end
)
"%ISCC%" installer.iss
if errorlevel 1 goto :fail

echo.
echo ===================================================
echo   Installer: installer\FitGirlToIDM-Setup-2.0.0.exe
echo ===================================================
goto :end

:fail
echo.
echo Build failed. See the messages above.
:end
echo.
pause
