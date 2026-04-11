@echo off
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --paths .. --onefile --name WinDrop_debug --add-data "assets;assets" main.py
echo.
echo Debug build complete. dist\WinDrop_debug.exe
pause
