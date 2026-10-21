@echo off
rem Builds dist\StickDriftFix.exe - a single portable file, no Python needed to run it.
cd /d "%~dp0.."
py -3 -m pip install -r requirements.txt pyinstaller || exit /b 1
py -3 tools\make_icon.py || exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StickDriftFix ^
  --icon assets\icon.ico ^
  --version-file tools\version_info.txt ^
  --add-data "stickfix\ui;ui" ^
  --exclude-module tkinter ^
  --collect-all vgamepad ^
  --collect-all sdl2dll ^
  --paths . ^
  tools\entry.py || exit /b 1
echo.
echo Built dist\StickDriftFix.exe
