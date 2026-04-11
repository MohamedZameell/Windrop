from __future__ import annotations

import asyncio
import plistlib
import socket
import ssl
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from windrop.core.certs import get_cert_path, get_key_path
from windrop.utils.config import load_config
from windrop.utils.logger import get_logger

HOST = "0.0.0.0"
PORT = 8771
UPLOAD_CHUNK_SIZE = 8192
ASK_TIMEOUT_SECONDS = 60


class ReceiverServer:
    def __init__(
        self,
        *,
        receive_folder: Path | None = None,
        receiver_name: str | None = None,
        receiver_model: str = "Windows",
        ask_timeout_seconds: int = ASK_TIMEOUT_SECONDS,
        bind_address: str = HOST,
        logger=None,
    ) -> None:
        config = load_config()
        self._receive_folder = Path(receive_folder or config.receive_folder)
        self._receiver_name = receiver_name or socket.gethostname()
        self._receiver_model = receiver_model
        self._ask_timeout_seconds = ask_timeout_seconds
        self._bind_address = bind_address
        self._logger = logger or get_logger()

        self._on_ask: Callable[[str, list[dict[str, Any]]], bool | Awaitable[bool]] = (
            lambda _sender, _files: False
        )
        self._on_progress: Callable[[str, int, int | None], Any] = lambda *_args: None
        self._on_complete: Callable[[str, str], Any] = lambda *_args: None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._started_event = threading.Event()
        self._stop_requested = False
        self._last_error: Exception | None = None

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._pending_file_sizes: dict[str, int | None] = {}

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    @property
    def receive_folder(self) -> Path:
        return self._receive_folder

    def update_settings(self, *, receive_folder: Path, receiver_name: str) -> None:
        self._receive_folder = Path(receive_folder)
        self._receiver_name = receiver_name
        self._receive_folder.mkdir(parents=True, exist_ok=True)

    def update_bind_address(self, bind_address: str) -> None:
        """Change the bind address (e.g. to a Wi-Fi Direct adapter IP)."""
        self._bind_address = bind_address

    def set_on_ask(
        self, callback: Callable[[str, list[dict[str, Any]]], bool | Awaitable[bool]]
    ) -> None:
        self._on_ask = callback

    def set_on_progress(self, callback: Callable[[str, int, int | None], Any]) -> None:
        self._on_progress = callback

    def set_on_complete(self, callback: Callable[[str, str], Any]) -> None:
        self._on_complete = callback

    async def start(self) -> None:
        self._ensure_loop_thread()
        await self._run_on_loop(self._start_impl())
        if self._last_error is not None:
            raise RuntimeError("Failed to start receiver server") from self._last_error

    async def stop(self) -> None:
        if self._stop_requested:
            return

        self._stop_requested = True
        if self._loop is not None:
            try:
                await self._run_on_loop(self._stop_impl())
            except asyncio.CancelledError:
                pass

        loop = self._loop
        thread = self._thread
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

        self._loop = None
        self._thread = None
        self._app = None
        self._runner = None
        self._site = None
        self._ssl_context = None
        self._loop_ready.clear()
        self._started_event.clear()

    def _ensure_loop_thread(self) -> None:
        if self._thread and self._thread.is_alive() and self._loop is not None:
            return

        self._stop_requested = False
        self._last_error = None
        self._loop_ready.clear()
        self._started_event.clear()
        self._thread = threading.Thread(
            target=self._loop_worker,
            name="WinDropReceiverLoop",
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
            raise RuntimeError("Receiver server event loop is not available.")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        await asyncio.wrap_future(future)

    async def _start_impl(self) -> None:
        if self._site is not None:
            return

        try:
            self._receive_folder.mkdir(parents=True, exist_ok=True)
            self._ssl_context = self._build_ssl_context()
            self._app = web.Application()
            self._app.router.add_post("/Discover", self._handle_discover)
            self._app.router.add_post("/Ask", self._handle_ask)
            self._app.router.add_post("/Upload", self._handle_upload)

            self._runner = web.AppRunner(self._app, access_log=None)
            await self._runner.setup()

            self._site = web.TCPSite(
                self._runner,
                host=self._bind_address,
                port=PORT,
                ssl_context=self._ssl_context,
            )
            await self._site.start()
            self._started_event.set()
            self._logger.info("Receiver server listening on %s:%s", self._bind_address, PORT)
        except Exception as exc:
            self._last_error = exc
            self._started_event.set()
            raise

    async def _stop_impl(self) -> None:
        try:
            if self._site is not None:
                await self._site.stop()
            if self._runner is not None:
                await self._runner.cleanup()
        except asyncio.CancelledError:
            raise
        finally:
            self._site = None
            self._runner = None
            self._app = None
            self._pending_file_sizes.clear()
            self._logger.info("Receiver server stopped")

    async def _handle_discover(self, request: web.Request) -> web.Response:
        self._log_request(request, "/Discover")
        try:
            body = await request.read()
            if body:
                plistlib.loads(body)
        except Exception:
            pass

        response_data = plistlib.dumps(
            {
                "ReceiverComputerName": self._receiver_name,
                "ReceiverModelName": self._receiver_model,
                "ReceiverMediaCapabilities": {"version": 1},
            },
            fmt=plistlib.FMT_BINARY,
        )
        return web.Response(
            body=response_data,
            status=200,
            content_type="application/octet-stream",
        )

    async def _handle_ask(self, request: web.Request) -> web.Response:
        self._log_request(request, "/Ask")
        body = await request.read()
        payload = plistlib.loads(body)

        sender_name = self._extract_sender_name(payload)
        files = self._extract_files(payload)
        self._pending_file_sizes = {
            file_info["name"]: file_info.get("size")
            for file_info in files
            if file_info.get("name")
        }

        try:
            accepted = await asyncio.wait_for(
                self._invoke_ask_callback(sender_name, files),
                timeout=self._ask_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._logger.warning("Ask request timed out for sender %s", sender_name)
            return web.Response(status=403, text="Declined")

        if accepted:
            return web.Response(
                body=plistlib.dumps({}, fmt=plistlib.FMT_BINARY),
                status=200,
                content_type="application/octet-stream",
            )
        return web.Response(status=403)

    async def _handle_upload(self, request: web.Request) -> web.Response:
        self._log_request(request, "/Upload")
        content_type = request.content_type or ""

        if "multipart" in content_type:
            return await self._handle_upload_multipart(request)

        # Standard AirDrop: raw body (gzipped CPIO archive or single file)
        body = await request.read()
        if not body:
            return web.Response(status=400)

        self._receive_folder.mkdir(parents=True, exist_ok=True)

        from windrop.utils.cpio import extract_cpio_gzip

        saved = extract_cpio_gzip(body, self._receive_folder)
        if saved:
            for path in saved:
                await self._invoke_callback(self._on_complete, path.name, str(path))
        else:
            # Fallback: save raw body using filename from /Ask metadata
            filename = next(iter(self._pending_file_sizes), "received_file")
            target = self._resolve_target_path(filename)
            target.write_bytes(body)
            await self._invoke_callback(self._on_complete, target.name, str(target))

        self._pending_file_sizes.clear()
        return web.Response(status=200, text="OK")

    async def _handle_upload_multipart(self, request: web.Request) -> web.Response:
        """Fallback handler for multipart uploads."""
        reader = await request.multipart()
        saved_files: list[str] = []

        try:
            while True:
                part = await reader.next()
                if part is None:
                    break
                if not getattr(part, "filename", None):
                    await part.read()
                    continue

                filename = Path(part.filename).name
                target_path = self._resolve_target_path(filename)
                total_bytes = self._pending_file_sizes.get(filename)
                bytes_received = 0

                with target_path.open("wb") as handle:
                    while True:
                        chunk = await part.read_chunk(UPLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        bytes_received += len(chunk)
                        await self._invoke_callback(self._on_progress, filename, bytes_received, total_bytes)

                saved_files.append(str(target_path))
                await self._invoke_callback(self._on_complete, filename, str(target_path))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Upload handling failed")
            raise

        return web.Response(status=200, text="OK")

    async def _invoke_ask_callback(self, sender_name: str, files: list[dict[str, Any]]) -> bool:
        callback = self._on_ask
        if asyncio.iscoroutinefunction(callback):
            return bool(await callback(sender_name, files))
        return bool(await asyncio.to_thread(callback, sender_name, files))

    async def _invoke_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        if asyncio.iscoroutinefunction(callback):
            await callback(*args)
            return
        await asyncio.to_thread(callback, *args)

    def _build_ssl_context(self) -> ssl.SSLContext:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=get_cert_path(), keyfile=get_key_path())
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    def _extract_sender_name(self, payload: dict[str, Any]) -> str:
        candidates = [
            payload.get("SenderComputerName"),
            payload.get("SenderID"),
            payload.get("SenderName"),
        ]
        sender_record = payload.get("SenderRecord")
        if isinstance(sender_record, dict):
            candidates.extend(
                [
                    sender_record.get("ComputerName"),
                    sender_record.get("FirstName"),
                    sender_record.get("FormattedName"),
                ]
            )

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "Unknown Sender"

    def _extract_files(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        files = payload.get("Files", [])
        extracted: list[dict[str, Any]] = []

        if not isinstance(files, list):
            return extracted

        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            extracted.append(
                {
                    "name": file_entry.get("FileName", "Unknown"),
                    "size": self._coerce_int(file_entry.get("FileSize")),
                    "type": file_entry.get("FileType", "application/octet-stream"),
                    "bid": file_entry.get("FileBID"),
                }
            )

        return extracted

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_target_path(self, filename: str) -> Path:
        candidate = self._receive_folder / filename
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            next_candidate = self._receive_folder / f"{stem} ({counter}){suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1

    def _log_request(self, request: web.Request, endpoint: str) -> None:
        sender_ip = request.transport.get_extra_info("peername")
        client_host = sender_ip[0] if isinstance(sender_ip, tuple) and sender_ip else "unknown"
        self._logger.info("HTTP %s from %s to %s", request.method, client_host, endpoint)
