# WinDrop

**WON'T WORK - NOT POSSIBLE FOR NOW AS OF APRIL 19, 2026**

WinDrop is a research prototype that attempted to bring true AirDrop-style file transfer to Windows for nearby iPhones. The codebase contains a substantial amount of discovery, BLE, TLS, plist, packaging, GUI, and transport work, but the project is not currently capable of delivering real, reliable AirDrop interoperability from a normal Windows desktop application.

This repository is being kept as a documented prototype and reference point, not as a finished working product.

## Why It Cannot Be Done Right Now

The short version is that the missing piece is not the Python GUI, not the packaging, and not the HTTP endpoints. The real blocker is the transport layer that Apple devices expect and the public APIs that Windows does not expose.

### 1. Old AirDrop depended on AWDL

Classic AirDrop relies on Apple's proprietary peer-to-peer Wi-Fi transport known as AWDL (Apple Wireless Direct Link). AWDL is not the same thing as normal Wi-Fi, local HTTP, or Wi-Fi Direct. It needs low-level wireless control such as:

- raw 802.11 frame access
- frame injection
- tight timing and channel hopping
- peer-to-peer service discovery on top of that transport

Windows does not provide a practical public app path for building that stack from a normal desktop application.

### 2. Wi-Fi Direct is not the same thing

One of the later directions in this project was to test whether Windows Wi-Fi Direct could replace AWDL. That turned out to be the wrong path for true iPhone AirDrop compatibility.

Wi-Fi Direct may work for Windows-to-Windows or for unrelated peer networking scenarios, but it is not a drop-in replacement for the AirDrop transport Apple devices expect.

### 3. iOS 26 likely changed the realistic path to Wi-Fi Aware

Recent research and reverse-engineering signals strongly suggest that modern interoperability with iOS 26 is more likely to happen through Wi-Fi Aware / NAN rather than through old-school AWDL recreation on Windows.

Google Quick Share and Samsung Quick Share appear to have succeeded because:

- iOS 26 supports Wi-Fi Aware for communication with non-Apple devices
- Android already exposes public Wi-Fi Aware APIs
- Google and Samsung could build on top of a nearby peer transport already supported by Android

Windows does not currently expose an equivalent public app-facing Wi-Fi Aware / NAN API for normal desktop developers.

### 4. Windows does not give us the public API we need

As of today's date, Windows publicly exposes app-facing support for:

- normal Wi-Fi networking
- Wi-Fi Direct
- standard WLAN operations

But it does not expose a public developer path comparable to Android's `WifiAwareManager` for Wi-Fi Aware / NAN peer discovery and session establishment.

That means this project cannot follow the same path Google and Samsung likely used for iOS 26 interoperability.

### 5. A custom Windows driver is not a realistic product path

In theory, a custom Windows Wi-Fi driver or driver-plus-firmware path might allow deeper access. In practice, that would mean:

- chipset-specific kernel-level development
- likely firmware and vendor-stack limitations
- driver signing and deployment problems
- very high fragility across Windows and driver updates

That is not realistic for this project, and it is far outside the scope of a normal installable Windows `.exe`.

## What We Built So Far

Even though the final product is not viable today, a large amount of the application and protocol work was implemented.

### Current prototype pieces

- a Windows desktop GUI using `customtkinter`
- drag-and-drop file staging
- device cards and send/receive panels
- system tray behavior
- settings storage in `%APPDATA%`
- self-signed TLS certificate generation
- async HTTPS receiver endpoints:
  - `/Discover`
  - `/Ask`
  - `/Upload`
- plist parsing for AirDrop-style request handling
- sender logic for outbound AirDrop-style requests
- BLE probing / advertisement scaffolding
- mDNS / zeroconf discovery work
- CPIO packaging support for transfers
- experimental Wi-Fi Direct integration
- PyInstaller packaging into a single `.exe`

### What this means in practice

The repository contains a lot of real engineering work, but it is still only a half-baked prototype because the foundational Windows transport needed for true iPhone AirDrop is not available to us in a usable, supported way.

So the current code should be understood as:

- a research prototype
- a UI and protocol experiment
- a reference for what was tried
- not a finished or functional Windows AirDrop client

## Project Status

This project is currently blocked by platform limitations, not by missing effort in the Python application code.

The most honest current status is:

- the GUI exists
- the packaging exists
- multiple experimental transport paths were tried
- true Windows-to-iPhone AirDrop is still not achievable here as a normal end-user Windows application

## What Would Need to Change

This project only becomes realistically finishable if Microsoft exposes a supported public path for the nearby peer transport that modern iPhone interoperability expects, such as a public Wi-Fi Aware / NAN API for Windows apps.

Until Windows allows that, or until some official equivalent path exists, WinDrop remains incomplete.

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
- The current repository state should be treated as a prototype snapshot, not a production-ready solution.
