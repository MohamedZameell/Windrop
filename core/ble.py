from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from bleak import BleakScanner
from bleak.exc import BleakError

from windrop.utils.logger import get_logger

APPLE_COMPANY_ID = 0x004C  # 76 decimal — same as Google Mosey uses
AIRDROP_SERVICE_UUID = "0000fcf1-0000-1000-8000-00805f9b34fb"
AIRDROP_MANUFACTURER_PAYLOAD = bytes([
    0x05, 0x12,                          # Type: AirDrop (5), Length: 18 bytes
    0x00, 0x00,                          # Version, flags
    0x00, 0x00, 0x00, 0x00,              # Contact hash (phone)
    0x00, 0x00, 0x00, 0x00,              # Contact hash (email)
    0x00, 0x00, 0x00, 0x00,              # Contact hash (Apple ID pt1)
    0x00, 0x00, 0x00, 0x00,              # Contact hash (Apple ID pt2)
])


async def check_bluetooth_available() -> bool:
    try:
        await BleakScanner.discover(timeout=1.5)
        return True
    except (BleakError, OSError):
        return False


class BLEAdvertiser:
    """Windows BLE advertiser with scanner probing and optional WinRT publishing."""

    def __init__(
        self,
        *,
        availability_checker: Callable[[], Awaitable[bool]] | None = None,
        publisher_factory: Callable[[], Any] | None = None,
        logger=None,
    ) -> None:
        self._availability_checker = availability_checker or check_bluetooth_available
        self._publisher_factory = publisher_factory
        self._logger = logger or get_logger()

        self.available = False
        self.running = False
        self.publisher_supported = False
        self.manufacturer_payload = AIRDROP_MANUFACTURER_PAYLOAD

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._publisher: Any | None = None
        self._loop_ready = threading.Event()
        self._stopped = False

    async def start(self) -> None:
        self._ensure_loop_thread()
        await self._run_on_loop(self._start_impl())

    async def stop(self) -> None:
        if self._stopped:
            return

        if self._loop is not None:
            await self._run_on_loop(self._stop_impl())

        thread = self._thread
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

        self._loop = None
        self._thread = None
        self._publisher = None
        self._loop_ready.clear()
        self._stopped = True

    def _ensure_loop_thread(self) -> None:
        if self._thread and self._thread.is_alive() and self._loop is not None:
            return

        self._stopped = False
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._loop_worker,
            name="WinDropBLELoop",
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

    async def _run_on_loop(self, coro: Awaitable[None]) -> None:
        if self._loop is None:
            raise RuntimeError("BLE background loop is not available.")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        await asyncio.wrap_future(future)

    async def _start_impl(self) -> None:
        if self.running:
            return

        self.available = await self._availability_checker()
        if not self.available:
            self.publisher_supported = False
            self._logger.info("Bluetooth adapter unavailable; BLE advertiser disabled.")
            return

        publisher_factory = self._publisher_factory or _load_winrt_publisher_factory(self._logger)
        if publisher_factory is None:
            self.publisher_supported = False
            self.running = True
            self._logger.warning(
                "WinRT BLE advertisement publisher is unavailable; continuing in scanner-only mode."
            )
            return

        try:
            publisher = publisher_factory()
            self._publisher = publisher
            self.publisher_supported = True
            _configure_advertisement_payload(publisher, self.manufacturer_payload)
            publisher.start()
            self.running = True
            self._logger.info("Started BLE advertisement publisher.")
        except Exception as exc:
            self._publisher = None
            self.publisher_supported = False
            self.running = True
            self._logger.warning(
                "BLE advertising is not available on this system; continuing without publisher: %s",
                exc,
            )

    async def _stop_impl(self) -> None:
        publisher = self._publisher
        self._publisher = None

        if publisher is not None:
            try:
                publisher.stop()
            except Exception as exc:
                self._logger.warning("Ignoring BLE publisher stop error: %s", exc)

        self.running = False


def _load_winrt_publisher_factory(logger) -> Callable[[], Any] | None:
    try:
        from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementPublisher
    except ImportError:
        logger.warning("WinRT BLE advertisement package is not installed.")
        return None

    return BluetoothLEAdvertisementPublisher


def _configure_advertisement_payload(publisher: Any, payload: bytes) -> None:
    from winrt.windows.devices.bluetooth.advertisement import BluetoothLEManufacturerData
    from winrt.windows.storage.streams import DataWriter

    # Manufacturer data: Apple company ID + AirDrop beacon (matches Google Mosey format)
    writer = DataWriter()
    writer.write_bytes(payload)
    manufacturer_data = BluetoothLEManufacturerData(APPLE_COMPANY_ID, writer.detach_buffer())
    publisher.advertisement.manufacturer_data.append(manufacturer_data)

    # Note: Windows BLE publisher can't combine manufacturer data + service UUIDs.
    # The Apple manufacturer data (company ID 0x4C + AirDrop beacon) is sufficient
    # for iPhone discovery. Google Mosey also uses the FCF1 service UUID, but on
    # Android the BLE stack supports both simultaneously.
