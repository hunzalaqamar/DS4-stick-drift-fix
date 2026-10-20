@echo off
title DS4 Stick Fix
cd /d "%~dp0"

rem Find a Python that has the required packages (skips the Microsoft Store stub).
set "PYEXE="
py -3 -c "import hid, vgamepad" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import hid, vgamepad" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYEXE "%%D\python.exe" -c "import hid, vgamepad" >nul 2>nul && set PYEXE="%%D\python.exe"
)

if not defined PYEXE (
    echo Python 3 with the hidapi and vgamepad packages was not found.
    echo.
    echo 1. Install Python 3 from https://www.python.org/downloads/
    echo 2. In this folder run:  py -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

%PYEXE% ds4_stick_fix.py %*
pause
