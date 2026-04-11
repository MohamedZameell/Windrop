# WinDrop

WinDrop is a Windows desktop app that can send and receive files with nearby iPhones over the local Wi-Fi network using the AirDrop discovery and transfer flow implemented in this project.

## Build

Run `build.bat` from the `windrop/` folder.

This creates `dist/WinDrop.exe`.

If the packaged app exits silently or you need console logs, run `build_debug.bat` instead.

That creates `dist/WinDrop_debug.exe`.

## Requirements

- Windows 10 version 1903 (build 18362) or newer, or Windows 11
- Python 3.11+ for local builds
- Bluetooth adapter recommended
- iPhone running iOS 26 or newer
- AirDrop on the iPhone set to `Everyone for 10 Minutes`
- Both devices connected to the same Wi-Fi network

## Notes

- `build.bat` installs dependencies from `requirements.txt`, installs PyInstaller, and packages a windowed single-file executable.
- `build_debug.bat` creates a console-enabled build that is easier to debug if the packaged app crashes without showing a window.
