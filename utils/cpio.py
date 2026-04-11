"""Minimal CPIO (newc / SVR4) archive read/write for AirDrop file transfer."""

from __future__ import annotations

import gzip
import io
from pathlib import Path

CPIO_MAGIC = b"070701"
CPIO_TRAILER = "TRAILER!!!"


def _pad4(n: int) -> int:
    return (n + 3) & ~3


def create_cpio_gzip(files: list[tuple[str, Path]]) -> bytes:
    """Create a gzipped CPIO newc archive from *(archive_name, file_path)* pairs."""
    raw = io.BytesIO()
    ino = 0
    for name, path in files:
        ino += 1
        data = path.read_bytes()
        _write_entry(raw, name, data, ino=ino)
    _write_entry(raw, CPIO_TRAILER, b"", ino=0)

    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb") as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def extract_cpio_gzip(data: bytes, output_dir: Path) -> list[Path]:
    """Extract a (possibly gzipped) CPIO archive from *data* into *output_dir*.

    Returns the list of extracted file paths, or an empty list if *data* is not
    a recognisable CPIO archive.
    """
    if len(data) >= 2 and data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:
            return []

    if len(data) < 110 or data[:6] != CPIO_MAGIC:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    offset = 0

    while offset + 110 <= len(data):
        hdr = data[offset : offset + 110]
        if hdr[:6] != CPIO_MAGIC:
            break

        namesize = int(hdr[94:102], 16)
        filesize = int(hdr[54:62], 16)

        name_start = offset + 110
        name_bytes = data[name_start : name_start + namesize]
        name = name_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")

        data_start = offset + _pad4(110 + namesize)

        if name == CPIO_TRAILER:
            break

        if filesize > 0:
            file_data = data[data_start : data_start + filesize]
            safe_name = Path(name).name  # strip directory components
            if safe_name:
                target = _unique_path(output_dir / safe_name)
                target.write_bytes(file_data)
                extracted.append(target)

        offset = data_start + _pad4(filesize)

    return extracted


def _write_entry(buf: io.BytesIO, name: str, data: bytes, *, ino: int = 0) -> None:
    name_bytes = name.encode("utf-8") + b"\x00"
    namesize = len(name_bytes)
    filesize = len(data)
    mode = 0o100644 if name != CPIO_TRAILER else 0

    header = (
        f"070701"
        f"{ino:08X}"
        f"{mode:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{1:08X}"
        f"{0:08X}"
        f"{filesize:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{namesize:08X}"
        f"{0:08X}"
    )
    buf.write(header.encode("ascii"))
    buf.write(name_bytes)
    buf.write(b"\x00" * (_pad4(110 + namesize) - 110 - namesize))
    buf.write(data)
    buf.write(b"\x00" * (_pad4(filesize) - filesize))


def _unique_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        alt = candidate.parent / f"{stem} ({counter}){suffix}"
        if not alt.exists():
            return alt
        counter += 1
