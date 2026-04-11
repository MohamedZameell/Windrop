from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from windrop.utils.config import get_app_dir


CERT_FILE_NAME = "cert.pem"
KEY_FILE_NAME = "key.pem"


def get_cert_path() -> str:
    cert_path, _ = ensure_certificate_files()
    return str(cert_path)


def get_key_path() -> str:
    _, key_path = ensure_certificate_files()
    return str(key_path)


def ensure_certificate_files() -> tuple[Path, Path]:
    app_dir = get_app_dir()
    cert_path = app_dir / CERT_FILE_NAME
    key_path = app_dir / KEY_FILE_NAME

    if cert_path.exists() and key_path.exists():
        _load_existing_certificate(cert_path, key_path)
        return cert_path, key_path

    _generate_self_signed_certificate(cert_path, key_path)
    return cert_path, key_path


def _generate_self_signed_certificate(cert_path: Path, key_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    common_name = "WinDrop"
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WinDrop"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )

    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName(common_name),
                ]
            ),
            critical=False,
        )
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _load_existing_certificate(cert_path: Path, key_path: Path) -> None:
    x509.load_pem_x509_certificate(cert_path.read_bytes())
    serialization.load_pem_private_key(key_path.read_bytes(), password=None)
