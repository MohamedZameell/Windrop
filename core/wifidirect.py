"""Wi-Fi Direct transport layer using WinRT APIs.

Manages a Wi-Fi Direct P2P group so that Apple devices (via AWDL/NAN)
can discover and connect to WinDrop.  Once the P2P link is up, the
virtual adapter's IP is used to bind mDNS and the HTTPS receiver.

Reference: jiaqizou-msft/Airdrop-on-Windows WiFiDirectTransport.cs
"""

from __future__ import annotations

import asyncio
import ipaddress
import threading
import time
from collections.abc import Callable
from typing import Any

from windrop.utils.logger import get_logger

# Lazy WinRT imports so the module can be imported even without the packages
_wfd = None
_de = None


def _ensure_winrt():
    global _wfd, _de
    if _wfd is None:
        import winrt.windows.devices.wifidirect as wfd
        import winrt.windows.devices.enumeration as de
        _wfd = wfd
        _de = de


class WiFiDirectManager:
    """Manages Wi-Fi Direct advertisement, listening, and connections."""

    def __init__(
        self,
        *,
        on_connection: Callable[[str], None] | None = None,
        on_disconnection: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_device_found: Callable[[str, str], None] | None = None,
        on_device_lost: Callable[[str], None] | None = None,
        logger=None,
    ) -> None:
        self._logger = logger or get_logger()
        self._on_connection = on_connection or (lambda ip: None)
        self._on_disconnection = on_disconnection or (lambda ip: None)
        self._on_status_change = on_status_change or (lambda msg: None)
        self._on_device_found = on_device_found or (lambda name, dev_id: None)
        self._on_device_lost = on_device_lost or (lambda name: None)

        self._publisher = None
        self._listener = None
        self._watcher = None
        self._connected_device = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._stopped = False

        self._local_ip: str | None = None
        self._remote_ip: str | None = None
        self._active = False

        # Track discovered Apple devices: {device_id: device_name}
        self._discovered_devices: dict[str, str] = {}
        self._use_general_watcher = False  # True when fallback unfiltered watcher is used

    @property
    def local_ip(self) -> str | None:
        """IP address of the Wi-Fi Direct virtual adapter (available after P2P link up)."""
        return self._local_ip

    @property
    def remote_ip(self) -> str | None:
        """Remote peer's IP on the P2P link."""
        return self._remote_ip

    @property
    def is_active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start Wi-Fi Direct advertisement and connection listener."""
        _ensure_winrt()
        self._stopped = False
        self._ensure_loop_thread()

        future = asyncio.run_coroutine_threadsafe(self._start_impl(), self._loop)
        future.result(timeout=10)

    def stop(self) -> None:
        """Stop everything and clean up."""
        if self._stopped:
            return
        self._stopped = True

        if self._loop is not None:
            future = asyncio.run_coroutine_threadsafe(self._stop_impl(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass

        loop = self._loop
        thread = self._thread
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

        self._loop = None
        self._thread = None
        self._active = False

    # ------------------------------------------------------------------
    # Internal event loop
    # ------------------------------------------------------------------

    def _ensure_loop_thread(self) -> None:
        if self._thread and self._thread.is_alive() and self._loop is not None:
            return

        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._loop_worker,
            name="WinDropWiFiDirect",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(timeout=5.0)

    def _loop_worker(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    # ------------------------------------------------------------------
    # Start/stop implementations (run on the background event loop)
    # ------------------------------------------------------------------

    async def _start_impl(self) -> None:
        try:
            # 1. Start Wi-Fi Direct advertisement publisher (become Group Owner)
            self._start_publisher()

            # 2. Start connection listener for incoming P2P connections
            self._start_listener()

            # 3. Start device watcher to find nearby Wi-Fi Direct peers
            self._start_watcher()

            self._active = True
            self._on_status_change("Wi-Fi Direct active")
            self._logger.info("Wi-Fi Direct manager started")
        except Exception as exc:
            self._logger.error("Failed to start Wi-Fi Direct: %s", exc)
            self._on_status_change(f"Wi-Fi Direct error: {exc}")
            raise

    async def _stop_impl(self) -> None:
        self._stop_watcher()
        self._stop_listener()
        self._stop_publisher()
        self._disconnect_device()
        self._active = False
        self._local_ip = None
        self._remote_ip = None
        self._logger.info("Wi-Fi Direct manager stopped")

    # ------------------------------------------------------------------
    # Advertisement Publisher
    # ------------------------------------------------------------------

    def _start_publisher(self) -> None:
        """Start Wi-Fi Direct advertisement as autonomous Group Owner."""
        self._publisher = _wfd.WiFiDirectAdvertisementPublisher()

        # Become the Group Owner so peers can join us
        self._publisher.advertisement.is_autonomous_group_owner_enabled = True
        # Disable legacy (non-P2P) settings — we want pure Wi-Fi Direct
        self._publisher.advertisement.legacy_settings.is_enabled = False
        # Normal discoverability
        self._publisher.advertisement.listen_state_discoverability = (
            _wfd.WiFiDirectAdvertisementListenStateDiscoverability.NORMAL
        )

        self._publisher.add_status_changed(self._on_publisher_status_changed)
        self._publisher.start()
        self._logger.info(
            "Wi-Fi Direct publisher started (status=%s)", self._publisher.status
        )

    def _stop_publisher(self) -> None:
        if self._publisher is not None:
            try:
                self._publisher.stop()
            except Exception as exc:
                self._logger.warning("Publisher stop error: %s", exc)
            self._publisher = None

    def _on_publisher_status_changed(self, sender, args) -> None:
        status = args.status
        error = args.error
        status_name = _publisher_status_name(status)
        self._logger.info(
            "Wi-Fi Direct publisher: status=%s error=%s", status_name, error
        )
        self._on_status_change(f"WiFi Direct: {status_name}")

        # If publisher entered an error state, try to recover
        if status == _wfd.WiFiDirectAdvertisementPublisherStatus.ABORTED:
            self._logger.warning("Publisher aborted (error=%s), will retry in 3s", error)
            if self._loop and not self._stopped:
                self._loop.call_later(3.0, self._retry_publisher)

    def _retry_publisher(self) -> None:
        if self._stopped:
            return
        try:
            self._stop_publisher()
            self._start_publisher()
        except Exception as exc:
            self._logger.error("Publisher retry failed: %s", exc)

    # ------------------------------------------------------------------
    # Connection Listener (incoming P2P connections)
    # ------------------------------------------------------------------

    def _start_listener(self) -> None:
        """Listen for incoming Wi-Fi Direct connection requests."""
        self._listener = _wfd.WiFiDirectConnectionListener()
        self._listener.add_connection_requested(self._on_connection_requested)
        self._logger.info("Wi-Fi Direct connection listener started")

    def _stop_listener(self) -> None:
        if self._listener is not None:
            self._listener = None

    def _on_connection_requested(self, sender, args) -> None:
        """Handle an incoming Wi-Fi Direct connection request."""
        try:
            request = args.get_connection_request()
            device_info = request.device_information
            self._logger.info(
                "Wi-Fi Direct connection request from: %s (id=%s)",
                device_info.name,
                device_info.id,
            )

            # Accept the connection on the background loop
            if self._loop and not self._stopped:
                asyncio.run_coroutine_threadsafe(
                    self._accept_connection(device_info.id), self._loop
                )
        except Exception as exc:
            self._logger.error("Error handling connection request: %s", exc)

    async def _accept_connection(self, device_id: str) -> None:
        """Accept an incoming Wi-Fi Direct connection."""
        try:
            device = await _wfd.WiFiDirectDevice.from_id_async(device_id)
            if device is None:
                self._logger.error("Failed to get WiFiDirectDevice for %s", device_id)
                return

            endpoint_pairs = device.get_connection_endpoint_pairs()
            if endpoint_pairs.size == 0:
                self._logger.error("No endpoint pairs from %s", device_id)
                return

            ep = endpoint_pairs.get_at(0)
            local_host = ep.local_host_name
            remote_host = ep.remote_host_name

            self._local_ip = local_host.display_name if local_host else None
            self._remote_ip = remote_host.display_name if remote_host else None
            self._connected_device = device

            self._logger.info(
                "Wi-Fi Direct connected: local=%s remote=%s",
                self._local_ip,
                self._remote_ip,
            )
            self._on_status_change(
                f"P2P connected: {self._remote_ip}"
            )
            if self._remote_ip:
                self._on_connection(self._remote_ip)

        except Exception as exc:
            self._logger.error("Failed to accept connection: %s", exc)

    # ------------------------------------------------------------------
    # Device Watcher (find nearby Wi-Fi Direct peers)
    # ------------------------------------------------------------------

    def _start_watcher(self) -> None:
        """Watch for nearby Wi-Fi Direct peers.

        Python WinRT 3.x only exposes the no-arg create_watcher() overload, so
        we must use the general device watcher and filter results ourselves to
        reject USB-enumerated devices.
        """
        self._use_general_watcher = True
        self._watcher = _de.DeviceInformation.create_watcher()

        self._watcher.add_added(self._on_device_added)
        self._watcher.add_updated(self._on_device_updated)
        self._watcher.add_removed(self._on_device_removed)
        self._watcher.add_enumeration_completed(self._on_enum_completed)
        self._watcher.start()
        self._logger.info("Device watcher started (filtered general watcher)")

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            try:
                # Only stop if it's in a running state
                status = self._watcher.status
                if status in (
                    _de.DeviceWatcherStatus.STARTED,
                    _de.DeviceWatcherStatus.ENUMERATION_COMPLETED,
                ):
                    self._watcher.stop()
            except Exception as exc:
                self._logger.warning("Watcher stop error: %s", exc)
            self._watcher = None

    def _on_device_added(self, sender, device_info) -> None:
        name = device_info.name or ""
        dev_id = device_info.id or ""

        # When using the unfiltered general watcher, reject USB device IDs
        # USB IDs look like: \\?\USB#VID_05AC&PID_12A8#...
        if self._use_general_watcher and ("\\\\?\\USB#" in dev_id or "USB#VID_" in dev_id):
            return

        if self._is_airdrop_candidate(name, device_info):
            # Deduplicate by name (multiple entries for same device are common)
            if name not in self._discovered_devices.values():
                self._discovered_devices[dev_id] = name
                self._logger.info(
                    "Wi-Fi Direct peer found: %s (id=%s)", name, dev_id[:60]
                )
                self._on_device_found(name, dev_id)

    def _on_device_updated(self, sender, device_update) -> None:
        pass

    def _on_device_removed(self, sender, device_update) -> None:
        dev_id = device_update.id if hasattr(device_update, "id") else str(device_update)
        name = self._discovered_devices.pop(dev_id, None)
        if name:
            self._logger.info("Wi-Fi Direct device removed: %s", name)
            self._on_device_lost(name)

    def _on_enum_completed(self, sender, obj) -> None:
        self._logger.info(
            "Wi-Fi Direct enumeration complete: %d Apple devices",
            len(self._discovered_devices),
        )

    @property
    def discovered_devices(self) -> dict[str, str]:
        """Return {device_id: device_name} of discovered Apple devices."""
        return dict(self._discovered_devices)

    @staticmethod
    def _is_airdrop_candidate(name: str, device_info) -> bool:
        """Check if a discovered device might be an Apple AirDrop peer."""
        lower = name.lower()
        return any(
            keyword in lower
            for keyword in ("iphone", "ipad", "macbook", "apple", "mac")
        )

    # ------------------------------------------------------------------
    # Outgoing connection (for sending files)
    # ------------------------------------------------------------------

    def connect_to_device_sync(self, device_id: str) -> tuple[str, str] | None:
        """Connect to a Wi-Fi Direct device (blocking, called from any thread).

        Returns (local_ip, remote_ip) or None on failure.
        """
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(
            self._connect_to_device_async(device_id), self._loop
        )
        return future.result(timeout=30)

    async def _connect_to_device_async(self, device_id: str) -> tuple[str, str] | None:
        """Connect to a specific Wi-Fi Direct device."""
        _ensure_winrt()
        try:
            self._logger.info("Connecting to Wi-Fi Direct device: %s", device_id[:60])
            device = await _wfd.WiFiDirectDevice.from_id_async(device_id)
            if device is None:
                self._logger.error("from_id_async returned None for %s", device_id[:60])
                return None

            endpoint_pairs = device.get_connection_endpoint_pairs()
            if endpoint_pairs.size == 0:
                self._logger.error("No endpoint pairs for %s", device_id[:60])
                return None

            ep = endpoint_pairs.get_at(0)
            local_ip = ep.local_host_name.display_name if ep.local_host_name else None
            remote_ip = ep.remote_host_name.display_name if ep.remote_host_name else None

            if local_ip and remote_ip:
                self._local_ip = local_ip
                self._remote_ip = remote_ip
                self._connected_device = device
                self._logger.info(
                    "P2P link established: local=%s remote=%s", local_ip, remote_ip
                )
                self._on_connection(remote_ip)
                return (local_ip, remote_ip)

            return None
        except Exception as exc:
            self._logger.error("Failed to connect to device %s: %s", device_id[:60], exc)
            return None

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def _disconnect_device(self) -> None:
        if self._connected_device is not None:
            try:
                self._connected_device.close()
            except Exception:
                pass
            self._connected_device = None

    # ------------------------------------------------------------------
    # Utility: get Wi-Fi Direct adapter IP
    # ------------------------------------------------------------------

    def get_wifidirect_adapter_ip(self) -> str | None:
        """Scan network interfaces for a Wi-Fi Direct virtual adapter IP.

        When the publisher is active as Group Owner, Windows creates a
        virtual adapter (usually named 'Microsoft Wi-Fi Direct Virtual
        Adapter') with a 192.168.49.x address.
        """
        import socket
        import struct

        try:
            # On Windows, the Wi-Fi Direct GO adapter typically gets
            # 192.168.49.1 (the group owner address)
            for info in socket.getaddrinfo(socket.gethostname(), None):
                family, _, _, _, sockaddr = info
                if family != socket.AF_INET:
                    continue
                ip = sockaddr[0]
                if ip.startswith("192.168.49."):
                    self._logger.info("Found Wi-Fi Direct adapter IP: %s", ip)
                    return ip
        except Exception as exc:
            self._logger.debug("Error scanning for WFD adapter: %s", exc)

        # Fallback: try link-local range
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                family, _, _, _, sockaddr = info
                if family != socket.AF_INET:
                    continue
                ip = sockaddr[0]
                if ip.startswith("169.254."):
                    return ip
        except Exception:
            pass

        return self._local_ip


def _publisher_status_name(status: int) -> str:
    """Human-readable name for WiFiDirectAdvertisementPublisherStatus."""
    names = {0: "Created", 1: "Started", 2: "Stopped", 3: "Aborted"}
    return names.get(status, f"Unknown({status})")
