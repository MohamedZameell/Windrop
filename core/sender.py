from __future__ import annotations

import asyncio
import plistlib
import secrets
import socket
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp

from windrop.utils.cpio import create_cpio_gzip

UPLOAD_CHUNK_SIZE = 8192
ASK_TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 300
DISCOVER_TIMEOUT_SECONDS = 10

FILE_TYPE_MAP = {
    ".jpg": "public.jpeg",
    ".jpeg": "public.jpeg",
    ".png": "public.png",
    ".mp4": "public.mpeg-4",
    ".pdf": "public.pdf",
    ".txt": "public.plain-text",
    ".mp3": "public.mp3",
    ".zip": "public.zip",
}


def _make_ssl_context() -> ssl.SSLContext:
    """SSL context that skips certificate verification (AirDrop uses self-signed certs)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Sender:
    def __init__(self) -> None:
        self._sender_id = secrets.token_hex(6)
        self._on_progress: Callable[[str, int, int], Any] = lambda *_args: None
        self._on_complete: Callable[[str, bool], Any] = lambda *_args: None
        self._on_error: Callable[[str, str], Any] = lambda *_args: None

    def set_on_progress(self, callback: Callable[[str, int, int], Any]) -> None:
        self._on_progress = callback

    def set_on_complete(self, callback: Callable[[str, bool], Any]) -> None:
        self._on_complete = callback

    def set_on_error(self, callback: Callable[[str, str], Any]) -> None:
        self._on_error = callback

    async def send_files(self, target_ip: str, target_port: int, file_paths: list[str]) -> bool:
        files = [Path(path) for path in file_paths]
        connector = aiohttp.TCPConnector(ssl=_make_ssl_context())

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                # Step 1: Discover (best-effort — some receivers may not require it)
                await self._send_discover(session, target_ip, target_port)

                # Step 2: Ask for permission
                accepted = await self._send_ask(session, target_ip, target_port, files)
                if not accepted:
                    return False

                # Step 3: Upload files as gzipped CPIO archive
                return await self._upload_files(session, target_ip, target_port, files)
        finally:
            await connector.close()

    # ------------------------------------------------------------------
    # Step 1: Discover
    # ------------------------------------------------------------------

    async def _send_discover(
        self,
        session: aiohttp.ClientSession,
        target_ip: str,
        target_port: int,
    ) -> bool:
        url = f"https://{target_ip}:{target_port}/Discover"
        payload = plistlib.dumps(
            {
                "SenderComputerName": socket.gethostname(),
                "SenderModelName": "Windows",
                "SenderID": self._sender_id,
            },
            fmt=plistlib.FMT_BINARY,
        )
        try:
            async with session.post(
                url,
                data=payload,
                headers={"Content-Type": "application/octet-stream"},
                timeout=aiohttp.ClientTimeout(total=DISCOVER_TIMEOUT_SECONDS),
            ) as response:
                return response.status == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Step 2: Ask
    # ------------------------------------------------------------------

    async def _send_ask(
        self,
        session: aiohttp.ClientSession,
        target_ip: str,
        target_port: int,
        files: list[Path],
    ) -> bool:
        payload = self._build_ask_payload(files)
        ask_timeout = aiohttp.ClientTimeout(total=ASK_TIMEOUT_SECONDS)
        url = f"https://{target_ip}:{target_port}/Ask"

        try:
            async with session.post(
                url,
                data=plistlib.dumps(payload, fmt=plistlib.FMT_BINARY),
                headers={"Content-Type": "application/octet-stream"},
                timeout=ask_timeout,
            ) as response:
                if response.status == 200:
                    return True
                if response.status == 403:
                    for file_path in files:
                        await self._invoke_callback(
                            self._on_error, file_path.name, "Transfer declined by iPhone"
                        )
                    return False

                message = f"Ask request failed with status {response.status}"
                for file_path in files:
                    await self._invoke_callback(self._on_error, file_path.name, message)
                return False
        except asyncio.TimeoutError:
            for file_path in files:
                await self._invoke_callback(
                    self._on_error, file_path.name, "iPhone did not respond in time"
                )
            return False
        except aiohttp.ClientError as exc:
            for file_path in files:
                await self._invoke_callback(self._on_error, file_path.name, str(exc))
            return False

    # ------------------------------------------------------------------
    # Step 3: Upload (gzipped CPIO archive)
    # ------------------------------------------------------------------

    async def _upload_files(
        self,
        session: aiohttp.ClientSession,
        target_ip: str,
        target_port: int,
        files: list[Path],
    ) -> bool:
        url = f"https://{target_ip}:{target_port}/Upload"
        archive_data = create_cpio_gzip([(f.name, f) for f in files])
        total_bytes = len(archive_data)
        label = files[0].name if files else "upload"

        try:
            await self._invoke_callback(self._on_progress, label, 0, total_bytes)

            async with session.post(
                url,
                data=archive_data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(total_bytes),
                },
                timeout=aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT_SECONDS),
            ) as response:
                if response.status == 200:
                    await self._invoke_callback(self._on_progress, label, total_bytes, total_bytes)
                    for f in files:
                        await self._invoke_callback(self._on_complete, f.name, True)
                    return True

                msg = f"Upload failed with status {response.status}"
                for f in files:
                    await self._invoke_callback(self._on_error, f.name, msg)
                    await self._invoke_callback(self._on_complete, f.name, False)
                return False
        except asyncio.TimeoutError:
            for f in files:
                await self._invoke_callback(self._on_error, f.name, "Upload timed out")
                await self._invoke_callback(self._on_complete, f.name, False)
            return False
        except aiohttp.ClientError as exc:
            for f in files:
                await self._invoke_callback(self._on_error, f.name, str(exc))
                await self._invoke_callback(self._on_complete, f.name, False)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_ask_payload(self, files: list[Path]) -> dict[str, Any]:
        return {
            "SenderComputerName": socket.gethostname(),
            "BundleID": "com.apple.finder",
            "SenderModelName": "Windows",
            "SenderID": self._sender_id,
            "Files": [
                {
                    "FileName": file_path.name,
                    "FileType": self._detect_file_type(file_path),
                    "FileSize": file_path.stat().st_size,
                    "FileBID": secrets.token_hex(4),
                    "FileIsDirectory": False,
                    "ConversationID": self._sender_id,
                }
                for file_path in files
            ],
        }

    def _detect_file_type(self, file_path: Path) -> str:
        return FILE_TYPE_MAP.get(file_path.suffix.lower(), "public.data")

    async def _invoke_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        if asyncio.iscoroutinefunction(callback):
            await callback(*args)
            return
        await asyncio.to_thread(callback, *args)
