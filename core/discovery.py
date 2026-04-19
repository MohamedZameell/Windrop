from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf


SERVICE_TYPE = "_airdrop._tcp.local."
SERVICE_PORT = 8771
SERVICE_MODEL = "Windows"
NETWORK_POLL_INTERVAL_SECONDS = 3.0

# AirDrop receiver capability flags (bitmask)
# Must advertise at least SUPPORTS_MIXED_TYPES or SUPPORTS_PIPELINING
# for senders to consider this a valid receiver.
# macOS default = 1019 (all capabilities)
_SUPPORTS_URL = 0x01
_SUPPORTS_DVZIP = 0x02          # Upload body is gzipped CPIO (dvzip)
_SUPPORTS_PIPELINING = 0x04
_SUPPORTS_MIXED_TYPES = 0x08    # Required for multi-file transfers
_SUPPORTS_IRIS = 0x40
_SUPPORTS_DISCOVER_MAYBE = 0x80 # Required for /Discover handshake
_SUPPORTS_ASSET_BUNDLE = 0x200

SERVICE_FLAGS = str(
    _SUPPORTS_URL
    | _SUPPORTS_DVZIP
    | _SUPPORTS_MIXED_TYPES
    | _SUPPORTS_IRIS
    | _SUPPORTS_DISCOVER_MAYBE
    | _SUPPORTS_ASSET_BUNDLE
)  # = "715"


@dataclass(slots=True)
class DiscoveredDevice:
    name: str
    ip: str
    port: int


class _AirDropServiceListener(ServiceListener):
    def __init__(self, owner: "DiscoveryService") -> None:
        self._owner = owner

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._owner._handle_service_seen(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._owner._handle_service_seen(zc, type_, name)

    def remove_service(self, _zc: Zeroconf, _type: str, name: str) -> None:
        self._owner._handle_service_lost(name)


class DiscoveryService:
    """Registers and discovers AirDrop services on the active Wi-Fi interface."""

    def __init__(
        self,
        *,
        on_device_found: Callable[[str, str, int], None],
        on_device_lost: Callable[[str], None],
        device_name: str | None = None,
        hostname: str | None = None,
        port: int = SERVICE_PORT,
        interface_ip: str | None = None,
    ) -> None:
        self._on_device_found = on_device_found
        self._on_device_lost = on_device_lost
        self._hostname = hostname or socket.gethostname()
        self._device_name = device_name or f"WinDrop-{self._hostname}"
        self._port = port
        self._forced_interface_ip = interface_ip

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._service_info: ServiceInfo | None = None
        self._listener = _AirDropServiceListener(self)
        self._discovered_devices: dict[str, DiscoveredDevice] = {}
        self._bound_interface_ip: str | None = None
        self._last_error: Exception | None = None

    @property
    def service_name(self) -> str:
        return f"{self._device_name}.{SERVICE_TYPE}"

    @property
    def bound_interface_ip(self) -> str | None:
        return self._bound_interface_ip

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def update_device_name(self, device_name: str) -> None:
        with self._lock:
            self._device_name = device_name
        if self._thread and self._thread.is_alive():
            interface_ip = self._bound_interface_ip or self._select_active_wifi_ip()
            self._restart_discovery(interface_ip)

    def rebind_to_interface(self, interface_ip: str) -> None:
        """Rebind mDNS to a different network interface (e.g. Wi-Fi Direct adapter)."""
        with self._lock:
            self._forced_interface_ip = interface_ip
        if self._thread and self._thread.is_alive():
            self._restart_discovery(interface_ip)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._started_event.clear()
            self._thread = threading.Thread(
                target=self._run_worker,
                name="WinDropDiscovery",
                daemon=True,
            )
            self._thread.start()

        self._started_event.wait(timeout=5.0)
        if self._last_error is not None:
            raise RuntimeError("Failed to start discovery service") from self._last_error

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        self._started_event.clear()

    def _run_worker(self) -> None:
        self._last_error = None
        current_ip: str | None = None

        try:
            while not self._stop_event.is_set():
                # Use forced interface (Wi-Fi Direct adapter) if set
                next_ip = self._forced_interface_ip or self._select_active_wifi_ip()

                if next_ip != current_ip:
                    self._restart_discovery(next_ip)
                    current_ip = next_ip

                self._started_event.set()
                self._stop_event.wait(NETWORK_POLL_INTERVAL_SECONDS)
        except Exception as exc:
            self._last_error = exc
            self._started_event.set()
        finally:
            self._teardown_discovery()
            self._started_event.set()

    def _restart_discovery(self, interface_ip: str) -> None:
        self._teardown_discovery()

        # Collect both IPv4 and IPv6 addresses for the chosen interface
        addresses = [socket.inet_aton(interface_ip)]
        ipv6_addr = self._get_link_local_ipv6(interface_ip)
        if ipv6_addr is not None:
            addresses.append(ipv6_addr)

        zeroconf = Zeroconf(interfaces=[interface_ip])
        service_info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=self.service_name,
            addresses=addresses,
            port=self._port,
            properties={
                b"flags": SERVICE_FLAGS.encode("utf-8"),
            },
            server=f"{self._hostname}.local.",
        )

        zeroconf.register_service(service_info)
        browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener=self._listener)

        with self._lock:
            self._zeroconf = zeroconf
            self._service_info = service_info
            self._browser = browser
            self._bound_interface_ip = interface_ip

    def _teardown_discovery(self) -> None:
        with self._lock:
            zeroconf = self._zeroconf
            service_info = self._service_info
            self._browser = None
            self._zeroconf = None
            self._service_info = None
            self._bound_interface_ip = None

        if zeroconf is not None and service_info is not None:
            try:
                zeroconf.unregister_service(service_info)
            except Exception:
                pass

        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:
                pass

    def _handle_service_seen(self, zc: Zeroconf, type_: str, name: str) -> None:
        if name == self.service_name:
            return

        info = zc.get_service_info(type_, name, timeout=3000)
        if info is None:
            return

        addresses = info.parsed_addresses()
        if not addresses:
            return

        device = DiscoveredDevice(name=name, ip=addresses[0], port=info.port)
        with self._lock:
            previous = self._discovered_devices.get(name)
            if previous == device:
                return
            self._discovered_devices[name] = device

        self._on_device_found(device.name, device.ip, device.port)

    def _handle_service_lost(self, name: str) -> None:
        with self._lock:
            removed = self._discovered_devices.pop(name, None)
        if removed is not None:
            self._on_device_lost(name)

    def _select_active_wifi_ip(self) -> str:
        """Pick the best network interface for mDNS.

        Avoids Windows ICS/Mobile Hotspot adapters (192.168.137.x) and
        virtual adapters.  Prefers the interface with a default gateway
        (i.e. the one actually connected to a router or iPhone hotspot).
        """
        candidates: list[str] = []
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            if family != socket.AF_INET:
                continue
            ip = sockaddr[0]
            if ip.startswith("127.") or ip.startswith("192.168.137."):
                continue
            candidates.append(ip)

        if not candidates:
            # Last resort: include 192.168.137.x
            for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
                if family != socket.AF_INET:
                    continue
                ip = sockaddr[0]
                if not ip.startswith("127."):
                    candidates.append(ip)

        if not candidates:
            raise RuntimeError("No active IPv4 interface was found for WinDrop discovery.")

        # Prefer interface that can reach the internet (has a default route)
        for ip in candidates:
            if self._has_default_route(ip):
                return ip

        private = [ip for ip in candidates if self._is_private_ipv4(ip)]
        return private[0] if private else candidates[0]

    @staticmethod
    def _has_default_route(ip: str) -> bool:
        """Check if this interface can reach an external IP (has a gateway)."""
        import errno
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((ip, 0))
            s.settimeout(0)
            s.connect(("8.8.8.8", 53))
            return True
        except (OSError, socket.error):
            return False
        finally:
            s.close()

    @staticmethod
    def _get_link_local_ipv6(ipv4: str) -> bytes | None:
        """Find a link-local IPv6 address on the same interface as *ipv4*.

        AirDrop traditionally uses IPv6 link-local addresses, so advertising
        one alongside the IPv4 address improves discoverability.
        """
        try:
            import ipaddress
            target_v4 = ipaddress.ip_address(ipv4)
            # Walk all addresses; match the interface that also has our IPv4
            has_v4 = set()
            v6_by_idx: dict[int, bytes] = {}
            for info in socket.getaddrinfo(socket.gethostname(), None):
                family, _, _, _, sockaddr = info
                if family == socket.AF_INET:
                    if ipaddress.ip_address(sockaddr[0]) == target_v4:
                        # On Windows getaddrinfo doesn't give interface index for v4,
                        # but we can collect all fe80:: addresses as candidates
                        pass
                elif family == socket.AF_INET6:
                    addr = ipaddress.ip_address(sockaddr[0].split("%")[0])
                    if addr.is_link_local:
                        return addr.packed
        except Exception:
            pass
        return None

    @staticmethod
    def _is_private_ipv4(ip_address: str) -> bool:
        parts = ip_address.split(".")
        if len(parts) != 4:
            return False
        first = int(parts[0])
        second = int(parts[1])
        return (
            first == 10
            or (first == 172 and 16 <= second <= 31)
            or (first == 192 and second == 168)
        )
