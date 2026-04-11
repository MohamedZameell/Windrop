from __future__ import annotations

import asyncio
import queue
import socket
import threading
import uuid
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw
from tkinterdnd2 import TkinterDnD

from windrop.core.ble import BLEAdvertiser
from windrop.core.discovery import DiscoveryService
from windrop.core.receiver import ReceiverServer
from windrop.core.sender import Sender
from windrop.core.wifidirect import WiFiDirectManager
from windrop.gui.receive_panel import ReceivePanel
from windrop.gui.send_panel import SendPanel
from windrop.utils.config import (
    AppConfig,
    disable_startup,
    enable_startup,
    get_settings_manager,
    is_startup_enabled,
)
from windrop.utils.logger import get_logger


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, *, config: AppConfig, on_save) -> None:
        super().__init__(master)
        self.title("WinDrop Settings")
        self.geometry("460x320")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.configure(fg_color="#10151F")
        self._on_save = on_save
        self.after(10, self._center_over_parent)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        header = ctk.CTkLabel(self, text="Settings", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, columnspan=2, padx=24, pady=(24, 20), sticky="w")

        self._device_name = ctk.CTkEntry(self, placeholder_text="Device name")
        self._device_name.insert(0, config.device_name)
        self._device_name.grid(row=1, column=0, columnspan=2, padx=24, pady=(0, 14), sticky="ew")

        self._receive_folder = ctk.CTkEntry(self, placeholder_text="Receive folder")
        self._receive_folder.insert(0, str(config.receive_folder))
        self._receive_folder.grid(row=2, column=0, padx=(24, 10), pady=(0, 14), sticky="ew")

        browse_button = ctk.CTkButton(
            self,
            text="Browse",
            command=self._browse_receive_folder,
            width=86,
            height=36,
            corner_radius=12,
            fg_color="#242C3B",
            hover_color="#2D3647",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        browse_button.grid(row=2, column=1, padx=(0, 24), pady=(0, 14))

        self._startup_toggle = ctk.CTkSwitch(self, text="Start on Windows startup")
        if config.start_on_startup:
            self._startup_toggle.select()
        self._startup_toggle.grid(row=3, column=0, columnspan=2, padx=24, pady=(0, 10), sticky="w")

        self._tray_toggle = ctk.CTkSwitch(self, text="Minimize to tray on close")
        if config.minimize_to_tray_on_close:
            self._tray_toggle.select()
        self._tray_toggle.grid(row=4, column=0, columnspan=2, padx=24, pady=(0, 24), sticky="w")

        save_button = ctk.CTkButton(
            self,
            text="Save Settings",
            command=self._save,
            fg_color="#D98F2B",
            hover_color="#C8801B",
            text_color="#111111",
            height=40,
            corner_radius=14,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        save_button.grid(row=5, column=0, padx=(24, 10), pady=(0, 24), sticky="ew")

        cancel_button = ctk.CTkButton(
            self,
            text="Cancel",
            command=self.destroy,
            fg_color="#242C3B",
            hover_color="#2D3647",
            height=40,
            corner_radius=14,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        cancel_button.grid(row=5, column=1, padx=(0, 24), pady=(0, 24), sticky="ew")

    def _save(self) -> None:
        updated = AppConfig(
            device_name=self._device_name.get().strip() or socket.gethostname(),
            receive_folder=Path(self._receive_folder.get().strip()).expanduser(),
            start_on_startup=bool(self._startup_toggle.get()),
            minimize_to_tray_on_close=bool(self._tray_toggle.get()),
        )
        self._on_save(updated)
        self.destroy()

    def _browse_receive_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._receive_folder.get().strip() or str(Path.home()))
        if selected:
            self._receive_folder.delete(0, "end")
            self._receive_folder.insert(0, selected)

    def _center_over_parent(self) -> None:
        self.update_idletasks()
        parent_x = self.master.winfo_rootx()
        parent_y = self.master.winfo_rooty()
        parent_w = self.master.winfo_width()
        parent_h = self.master.winfo_height()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent_x + max((parent_w - width) // 2, 0)
        y = parent_y + max((parent_h - height) // 2, 0)
        self.geometry(f"+{x}+{y}")


class WinDropApp:
    def __init__(
        self,
        *,
        sender: Sender | None = None,
        enable_tray: bool = True,
        auto_start_services: bool = True,
    ) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.logger = get_logger()
        self.settings = get_settings_manager()
        self.config = self.settings.as_config()
        self.root = TkinterDnD.Tk()
        self.root.title("WinDrop")
        self.root.geometry("1220x760")
        self.root.minsize(1040, 680)
        self.root.configure(bg="#0B1018")
        self.root.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        self.sender = sender or Sender()
        self.sender.set_on_progress(self._handle_send_progress)
        self.sender.set_on_complete(self._handle_send_complete)
        self.sender.set_on_error(self._handle_send_error)

        self.discovery_service = DiscoveryService(
            on_device_found=self._on_device_found_from_service,
            on_device_lost=self._on_device_lost_from_service,
            device_name=f"WinDrop-{self.config.device_name}",
        )
        self.ble_advertiser = BLEAdvertiser()
        self.receiver_server = ReceiverServer(
            receive_folder=self.config.receive_folder,
            receiver_name=self.config.device_name,
        )
        self.wifidirect_manager = WiFiDirectManager(
            on_connection=self._on_wifidirect_connected,
            on_disconnection=self._on_wifidirect_disconnected,
            on_status_change=self._on_wifidirect_status,
            on_device_found=self._on_wifidirect_device_found,
            on_device_lost=self._on_wifidirect_device_lost,
        )
        # Map Wi-Fi Direct device IDs to display names
        self._wifidirect_device_ids: dict[str, str] = {}
        self.receiver_server.set_on_ask(self._handle_receiver_ask)
        self.receiver_server.set_on_progress(self._handle_receiver_progress)
        self.receiver_server.set_on_complete(self._handle_receiver_complete)

        self._enable_tray = enable_tray
        self._auto_start_services = auto_start_services
        self._selected_device_name: str | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._tray_icon: pystray.Icon | None = None
        self._tray_thread: threading.Thread | None = None
        self._send_thread: threading.Thread | None = None
        self._ui_queue: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._send_in_progress = False
        self._send_target_name: str | None = None
        self._shutdown_started = False
        self._pending_incoming: dict[str, dict[str, Any]] = {}

        self._build_ui()
        self.root.after(50, self._drain_ui_queue)
        if self._enable_tray:
            self._install_tray_icon()
        if self._auto_start_services:
            self._start_services()
        self._set_status("Searching for devices...")
        self.logger.info("WinDrop Phase 7 UI initialised")

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        top_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        top_bar.grid(row=0, column=0, padx=28, pady=(24, 14), sticky="ew")
        top_bar.grid_columnconfigure(0, weight=1)

        title_block = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_block.grid(row=0, column=0, sticky="w")

        title = ctk.CTkLabel(title_block, text="WinDrop", font=ctk.CTkFont(size=34, weight="bold"))
        title.grid(row=0, column=0, sticky="w")

        subtitle = ctk.CTkLabel(
            title_block,
            text="Windows AirDrop client shell for same-network transfers",
            text_color="#98A4B8",
            font=ctk.CTkFont(size=14),
        )
        subtitle.grid(row=1, column=0, pady=(4, 0), sticky="w")

        settings_button = ctk.CTkButton(
            top_bar,
            text="Settings",
            width=88,
            height=44,
            corner_radius=14,
            command=self._open_settings,
            fg_color="#131C29",
            hover_color="#182132",
            border_width=1,
            border_color="#2C3648",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        settings_button.grid(row=0, column=1, rowspan=2, sticky="e")

        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.grid(row=1, column=0, padx=28, pady=(0, 18), sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.send_panel = SendPanel(
            content,
            on_files_selected=self._handle_files_selected,
            on_send_clicked=self._handle_send_clicked,
        )
        self.send_panel.grid(row=0, column=0, padx=(0, 12), sticky="nsew")

        self.receive_panel = ReceivePanel(
            content,
            on_device_selected=self._handle_device_selected,
            on_incoming_action=self._handle_incoming_action,
            on_manual_connect=self._handle_manual_connect,
        )
        self.receive_panel.grid(row=0, column=1, padx=(12, 0), sticky="nsew")
        self.receive_panel.populate_incoming([])
        self.receive_panel.populate_devices([])

        status_bar = ctk.CTkFrame(
            self.root,
            fg_color="#10151F",
            corner_radius=18,
            border_width=1,
            border_color="#2C3648",
            height=58,
        )
        status_bar.grid(row=2, column=0, padx=28, pady=(0, 24), sticky="ew")
        status_bar.grid_columnconfigure(0, weight=1)

        self._status_label = ctk.CTkLabel(
            status_bar,
            text="Ready",
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._status_label.grid(row=0, column=0, padx=18, pady=16, sticky="w")

        self._status_detail = ctk.CTkLabel(
            status_bar,
            text=f"Receive folder: {self.config.receive_folder}",
            anchor="e",
            text_color="#98A4B8",
            font=ctk.CTkFont(size=13),
        )
        self._status_detail.grid(row=0, column=1, padx=18, pady=16, sticky="e")

    def _start_services(self) -> None:
        try:
            self.discovery_service.start()
        except Exception as exc:
            self.logger.exception("Discovery failed to start")
            self._set_status(f"Discovery error: {exc}")

        threading.Thread(target=self._run_async_service_start, args=(self.ble_advertiser.start,), daemon=True).start()
        threading.Thread(target=self._run_async_service_start, args=(self.receiver_server.start,), daemon=True).start()

        # Start Wi-Fi Direct (P2P transport for AirDrop)
        threading.Thread(target=self._start_wifidirect, daemon=True).start()

    def _run_async_service_start(self, coroutine_func) -> None:
        try:
            asyncio.run(coroutine_func())
        except Exception as exc:
            self.logger.exception("Async service failed to start")
            self._enqueue_ui_call(self._set_status, f"Service error: {exc}")

    def _on_device_found_from_service(self, name: str, ip: str, port: int) -> None:
        self._enqueue_ui_call(self._add_or_update_device, name, ip, port)

    def _on_device_lost_from_service(self, name: str) -> None:
        self._enqueue_ui_call(self._remove_device, name)

    def _add_or_update_device(self, name: str, ip: str, port: int) -> None:
        base_name = name.replace("._airdrop._tcp.local.", "")
        device = {
            "name": base_name,
            "subtitle": f"{ip}:{port} on this network",
            "status": "Ready to receive",
            "ip": ip,
            "port": port,
            "service_name": name,
        }
        self.receive_panel.add_device(device)
        self._set_status("Ready")

    def _remove_device(self, name: str) -> None:
        display_name = name.replace("._airdrop._tcp.local.", "")
        self.receive_panel.remove_device(display_name)
        if self._selected_device_name == display_name:
            self._selected_device_name = None
            self.send_panel.set_selected_device(None)
            if self._send_in_progress:
                self._handle_send_error(display_name, "Device disconnected")
        self._set_status("Searching for devices...")

    def _handle_manual_connect(self, ip: str, port: int) -> None:
        name = f"iPhone ({ip})"
        device = {
            "name": name,
            "subtitle": f"{ip}:{port} (manual)",
            "status": "Ready to receive",
            "ip": ip,
            "port": port,
            "service_name": name,
        }
        self.receive_panel.add_device(device)
        self._handle_device_selected(name)
        self._set_status(f"Connected to {ip}:{port}")
        self.logger.info("Manual connect: %s:%s", ip, port)

    # ------------------------------------------------------------------
    # Wi-Fi Direct callbacks
    # ------------------------------------------------------------------

    def _start_wifidirect(self) -> None:
        try:
            self.wifidirect_manager.start()
            self._enqueue_ui_call(self._set_status, "Wi-Fi Direct active — scanning for Apple devices")
        except Exception as exc:
            self.logger.exception("Wi-Fi Direct failed to start")
            self._enqueue_ui_call(self._set_status, f"Wi-Fi Direct error: {exc}")

    def _on_wifidirect_device_found(self, name: str, device_id: str) -> None:
        """Called when an Apple device is found via Wi-Fi Direct enumeration."""
        self._wifidirect_device_ids[name] = device_id
        self._enqueue_ui_call(self._add_wifidirect_device, name, device_id)

    def _on_wifidirect_device_lost(self, name: str) -> None:
        self._wifidirect_device_ids.pop(name, None)
        self._enqueue_ui_call(self._remove_device, name)

    def _add_wifidirect_device(self, name: str, device_id: str) -> None:
        device = {
            "name": name,
            "subtitle": "Wi-Fi Direct (tap to connect P2P)",
            "status": "Available via AirDrop",
            "ip": "",
            "port": 8771,
            "service_name": name,
            "wifidirect_id": device_id,
        }
        self.receive_panel.add_device(device)
        self._set_status(f"Found {name} via Wi-Fi Direct")

    def _on_wifidirect_connected(self, remote_ip: str) -> None:
        """Called when a P2P link is established."""
        self._enqueue_ui_call(self._handle_p2p_link_up, remote_ip)

    def _on_wifidirect_disconnected(self, remote_ip: str) -> None:
        self._enqueue_ui_call(self._set_status, "Wi-Fi Direct peer disconnected")

    def _on_wifidirect_status(self, message: str) -> None:
        self.logger.info("WiFi Direct status: %s", message)

    def _handle_p2p_link_up(self, remote_ip: str) -> None:
        """When a P2P link is established, rebind mDNS and receiver to the P2P interface."""
        local_ip = self.wifidirect_manager.local_ip
        if local_ip:
            self.logger.info(
                "P2P link up — rebinding services to %s (remote: %s)", local_ip, remote_ip
            )
            self.discovery_service.rebind_to_interface(local_ip)
            self._set_status(f"P2P connected to {remote_ip} — AirDrop ready")
        else:
            # Try to find the Wi-Fi Direct adapter IP
            wfd_ip = self.wifidirect_manager.get_wifidirect_adapter_ip()
            if wfd_ip:
                self.discovery_service.rebind_to_interface(wfd_ip)
                self._set_status(f"P2P active on {wfd_ip}")
            else:
                self._set_status(f"P2P connected to {remote_ip}")

    def _handle_device_selected(self, device_name: str) -> None:
        self._selected_device_name = device_name

        # If this is a Wi-Fi Direct device, initiate P2P connection
        device_id = self._wifidirect_device_ids.get(device_name)
        if device_id:
            self.send_panel.set_selected_device(device_name)
            self._set_status(f"Connecting to {device_name} via Wi-Fi Direct...")
            threading.Thread(
                target=self._connect_wifidirect_device,
                args=(device_name, device_id),
                daemon=True,
            ).start()
            return

        self.send_panel.set_selected_device(device_name)
        self._set_status(f"Ready to send to {device_name}")
        self.logger.info("Selected device: %s", device_name)

    def _connect_wifidirect_device(self, name: str, device_id: str) -> None:
        """Background thread: establish Wi-Fi Direct P2P link to a device."""
        try:
            result = self.wifidirect_manager.connect_to_device_sync(device_id)
            if result:
                local_ip, remote_ip = result
                # Update the device entry with the real IP
                self._enqueue_ui_call(
                    self._update_device_after_p2p, name, local_ip, remote_ip
                )
            else:
                self._enqueue_ui_call(
                    self._set_status, f"Could not establish P2P link to {name}"
                )
        except Exception as exc:
            self.logger.exception("Wi-Fi Direct connection failed")
            self._enqueue_ui_call(self._set_status, f"P2P error: {exc}")

    def _update_device_after_p2p(self, name: str, local_ip: str, remote_ip: str) -> None:
        """Update device card after P2P connection is established."""
        device = {
            "name": name,
            "subtitle": f"{remote_ip}:8771 via Wi-Fi Direct P2P",
            "status": "Connected — ready to send",
            "ip": remote_ip,
            "port": 8771,
            "service_name": name,
        }
        self.receive_panel.add_device(device)
        self._handle_device_selected(name)
        self._set_status(f"P2P link to {name} established ({remote_ip})")

    def _handle_files_selected(self, files: list[str]) -> None:
        if files:
            self._set_status(f"{len(files)} file(s) staged")
            self.logger.info("Staged %s file(s) for send", len(files))
        elif not self._send_in_progress:
            self._set_status("Searching for devices...")

    def _handle_send_clicked(self) -> None:
        if self._send_in_progress:
            return

        device = self.receive_panel.get_device(self._selected_device_name or "")
        files = self.send_panel.files
        if not device or not files:
            self._set_status("Select files and a device to continue")
            return

        self._send_in_progress = True
        self._send_target_name = str(device["name"])
        self.send_panel.start_send()
        self._set_status(f"Sending {len(files)} file(s) to {device['name']}...")

        self._send_thread = threading.Thread(
            target=self._run_send_worker,
            args=(str(device["ip"]), int(device["port"]), files),
            name="WinDropSendWorker",
            daemon=True,
        )
        self._send_thread.start()

    def _run_send_worker(self, target_ip: str, target_port: int, files: list[str]) -> None:
        try:
            success = asyncio.run(self.sender.send_files(target_ip, target_port, files))
        except Exception as exc:
            self.logger.exception("Send worker failed")
            self._enqueue_ui_call(self._finalise_send, False, str(exc))
            return

        if success:
            self._enqueue_ui_call(self._finalise_send, True, "Sent successfully")
        elif self._send_in_progress:
            self._enqueue_ui_call(self._finalise_send, False, "Transfer failed")

    def _finalise_send(self, success: bool, message: str) -> None:
        self._send_in_progress = False
        self._send_target_name = None
        if success:
            self.send_panel.finish_success()
            self._set_status("Ready")
        else:
            self.send_panel.finish_error(message)
            self._set_status(message)

    def _handle_send_progress(self, filename: str, bytes_sent: int, total_bytes: int) -> None:
        self._enqueue_ui_call(self.send_panel.update_progress, filename, bytes_sent, total_bytes)

    def _handle_send_complete(self, filename: str, success: bool) -> None:
        self.logger.info("Send complete for %s: %s", filename, success)

    def _handle_send_error(self, filename: str, error_message: str) -> None:
        self.logger.warning("Send error for %s: %s", filename, error_message)
        self._enqueue_ui_call(self._finalise_send, False, error_message)

    def _handle_receiver_ask(self, sender_name: str, files: list[dict[str, Any]]) -> bool:
        request_id = uuid.uuid4().hex
        decision_event = threading.Event()
        self._pending_incoming[request_id] = {
            "event": decision_event,
            "accepted": False,
            "title": files[0]["name"] if files else "Incoming transfer",
            "details": f"From {sender_name} - {len(files)} file(s)",
        }
        self._enqueue_ui_call(self._render_incoming_requests)
        decision_event.wait(timeout=55)
        decision = bool(self._pending_incoming.get(request_id, {}).get("accepted"))
        self._pending_incoming.pop(request_id, None)
        self._enqueue_ui_call(self._render_incoming_requests)
        return decision

    def _handle_receiver_progress(self, filename: str, bytes_received: int, total_bytes: int | None) -> None:
        if total_bytes:
            self._enqueue_ui_call(
                self._set_status,
                f"Receiving {filename} ({bytes_received}/{total_bytes} bytes)",
            )

    def _handle_receiver_complete(self, filename: str, filepath: str) -> None:
        self._enqueue_ui_call(self._set_status, f"Received {filename}")
        self.logger.info("Received file %s at %s", filename, filepath)

    def _handle_incoming_action(self, request_id: str, action: str) -> None:
        request = self._pending_incoming.get(request_id)
        if request is None:
            return
        request["accepted"] = action == "accept"
        request["event"].set()
        self._render_incoming_requests()
        verb = "accepted" if action == "accept" else "declined"
        self._set_status(f"Incoming transfer {verb}")

    def _render_incoming_requests(self) -> None:
        requests = [
            {
                "id": request_id,
                "title": item["title"],
                "details": item["details"],
            }
            for request_id, item in self._pending_incoming.items()
        ]
        self.receive_panel.populate_incoming(requests)

    def _open_settings(self) -> None:
        if self._settings_dialog and self._settings_dialog.winfo_exists():
            self._settings_dialog.focus()
            return
        self._settings_dialog = SettingsDialog(self.root, config=self.config, on_save=self._save_settings)

    def _save_settings(self, config: AppConfig) -> None:
        previous_device_name = self.config.device_name
        self.config = config
        self.settings.set("device_name", config.device_name)
        self.settings.set("receive_folder", str(config.receive_folder))
        self.settings.set("start_on_startup", config.start_on_startup)
        self.settings.set("minimize_to_tray_on_close", config.minimize_to_tray_on_close)

        if config.start_on_startup:
            enable_startup()
        else:
            disable_startup()

        self.receiver_server.update_settings(
            receive_folder=config.receive_folder,
            receiver_name=config.device_name,
        )
        if previous_device_name != config.device_name:
            self.discovery_service.update_device_name(f"WinDrop-{config.device_name}")

        self._status_detail.configure(text=f"Receive folder: {config.receive_folder}")
        self._set_status("Settings saved")
        self.logger.info("Saved settings to %s", config.receive_folder)

    def _set_status(self, message: str) -> None:
        if hasattr(self, "_status_label"):
            self._status_label.configure(text=message)

    def _handle_window_close(self) -> None:
        if self.config.minimize_to_tray_on_close and self._enable_tray:
            self.root.withdraw()
            self._set_status("WinDrop is still running in the system tray")
            return
        self._quit_application()

    def _install_tray_icon(self) -> None:
        image = self._create_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Show WinDrop", self._show_from_tray, default=True),
            pystray.MenuItem("Quit", self._quit_from_tray),
        )
        self._tray_icon = pystray.Icon("windrop", image, "WinDrop", menu)
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _show_from_tray(self, _icon=None, _item=None) -> None:
        self._enqueue_ui_call(self._restore_window)

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._set_status("Ready")

    def _quit_from_tray(self, _icon=None, _item=None) -> None:
        self._enqueue_ui_call(self._quit_application)

    def _quit_application(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True

        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None

        try:
            self.wifidirect_manager.stop()
        except Exception:
            self.logger.exception("Wi-Fi Direct shutdown failed")

        try:
            self.discovery_service.stop()
        except Exception:
            self.logger.exception("Discovery shutdown failed")

        for coroutine_func in (self.ble_advertiser.stop, self.receiver_server.stop):
            try:
                asyncio.run(coroutine_func())
            except Exception:
                self.logger.exception("Async service shutdown failed")

        self.logger.info("Shutting down WinDrop")
        self.root.destroy()

    def _enqueue_ui_call(self, callback, *args) -> None:
        self._ui_queue.put((callback, args))

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            callback(*args)

        if not self._shutdown_started:
            self.root.after(50, self._drain_ui_queue)

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), "#0B1018")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, 54, 54), radius=12, fill="#D98F2B")
        draw.polygon([(22, 30), (32, 18), (32, 26), (42, 26), (42, 42), (22, 42)], fill="#111111")
        return image
