@echo off
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --paths .. --onefile --windowed --name WinDrop --icon=assets/icon.ico --add-data "assets;assets" main.py
echo.
echo Build complete. Your exe is in the dist\ folder.
pause
