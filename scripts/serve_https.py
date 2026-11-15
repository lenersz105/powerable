"""Serve PowerAble over local HTTPS so phones can open the camera.

Mobile browsers only expose ``getUserMedia`` on a secure origin: HTTPS or
``localhost``. A plain ``http://192.168.x.x:5000`` address cannot start the
camera. This helper creates a self-signed certificate for this computer and
serves the same Flask app over TLS on the local network.

Usage (from the project root, with the virtual environment active):

    python scripts/serve_https.py

Then open the printed ``https://<computer-ip>:5443/`` address on the phone and
accept the one-time self-signed certificate warning.

The certificate is written to ``instance/certs/`` which is git-ignored. It is
for local development and a trusted home network only; never expose it to the
public internet.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

DEFAULT_PORT = 5443
CERT_FILENAME = "powerable-local.pem"
KEY_FILENAME = "powerable-local-key.pem"
CERT_VALID_DAYS = 800


def local_ipv4_addresses() -> list[str]:
    """Return usable IPv4 addresses for this computer."""

    addresses: set[str] = set()

    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            addresses.add(info[4][0])
    except OSError:
        pass

    # Asking the OS which source address it would use does not send packets.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    addresses.discard("0.0.0.0")
    addresses.discard("255.255.255.255")

    usable = []
    for value in sorted(addresses):
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local:
            continue
        usable.append(value)

    return usable


def _certificate_covers(cert_path: Path, key_path: Path, addresses: list[str]) -> bool:
    if not cert_path.exists() or not key_path.exists():
        return False

    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except (ValueError, x509.ExtensionNotFound, OSError):
        return False

    if certificate.not_valid_after_utc <= datetime.now(timezone.utc):
        return False

    covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    required = set(addresses) | {"127.0.0.1"}
    return required.issubset(covered)


def ensure_certificate(directory: Path, addresses: list[str], force: bool = False):
    """Create or reuse a self-signed certificate for localhost and LAN IPs."""

    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / CERT_FILENAME
    key_path = directory / KEY_FILENAME

    if not force and _certificate_covers(cert_path, key_path, addresses):
        return cert_path, key_path

    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "PowerAble Local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PowerAble Thesis Project"),
        ]
    )

    san_entries = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    for address in addresses:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(address)))

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CERT_VALID_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries), critical=False
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    try:
        key_path.chmod(0o600)
    except OSError:
        pass

    return cert_path, key_path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve PowerAble locally over HTTPS for phone camera access."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HTTPS_HOST", "0.0.0.0"),
        help="Interface to bind (default: 0.0.0.0, reachable on the LAN).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HTTPS_PORT", DEFAULT_PORT)),
        help=f"TLS port (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--force-cert",
        action="store_true",
        help="Regenerate the certificate even if a valid one exists.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    certificate_dir = PROJECT_ROOT / "instance" / "certs"
    addresses = local_ipv4_addresses()
    cert_path, key_path = ensure_certificate(
        certificate_dir, addresses, force=args.force_cert
    )

    from app import create_app  # Imported late so --help stays fast.

    app = create_app()

    print("PowerAble local HTTPS")
    print("=====================")
    print(f"Certificate : {cert_path}")
    print(f"Key         : {key_path}")
    print()
    print(f"Computer    : https://localhost:{args.port}/")
    if addresses:
        for address in addresses:
            print(f"Phone       : https://{address}:{args.port}/")
    else:
        print("Phone       : no LAN IPv4 address detected")
    print()
    print("On the phone, accept the self-signed certificate warning once.")
    print("Stop the server with Ctrl+C. Keep this on a trusted local network only.")
    print()

    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
        threaded=True,
        ssl_context=(str(cert_path), str(key_path)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
