"""Certificate watch: warns about expiring TLS certificates of your own services (RFC 1918 only)."""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from cryptography import x509

from sentinel.monitor.rules import Finding, _fp
from sentinel.permissions.models import Severity
from sentinel.permissions.netguard import is_rfc1918

if TYPE_CHECKING:
    from sentinel.services import Services

WARN_DAYS = 30
CHECK_INTERVAL_S = 6 * 3600


@dataclass
class CertStatus:
    target: str
    subject: str = ""
    not_after: float | None = None
    days_left: float | None = None
    error: str | None = None


def parse_target(target: str) -> tuple[str, int] | None:
    host, _, port = target.strip().rpartition(":")
    if not host or not port.isdigit() or not is_rfc1918(host):
        return None
    number = int(port)
    return (host, number) if 0 < number < 65536 else None


def fetch_certificate(host: str, port: int, timeout: float = 5.0) -> bytes:
    """Fetches the certificate without trust verification - only to read the expiry date."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:  # noqa: SIM117
        with context.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise ssl.SSLError("no certificate")
    return der


def describe(target: str, der: bytes, now: float | None = None) -> CertStatus:
    cert = x509.load_der_x509_certificate(der)
    not_after = cert.not_valid_after_utc.timestamp()
    names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    subject = str(names[0].value) if names else ""
    days = (not_after - (now or time.time())) / 86400
    return CertStatus(target, subject[:120], not_after, round(days, 1))


def finding_for(status: CertStatus) -> Finding | None:
    if status.days_left is None:
        return None
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    if status.days_left < 0:
        return Finding(
            rule="cert_expired",
            severity=Severity.CRITICAL,
            title=f"Certificate expired: {status.target} ({status.subject})",
            detail={"target": status.target, "days_left": status.days_left},
            fingerprint=_fp("cert_expired", status.target, day),
        )
    if status.days_left < WARN_DAYS:
        return Finding(
            rule="cert_expiring",
            severity=Severity.WARN,
            title=f"Certificate expires in {int(status.days_left)} days: {status.target}",
            detail={"target": status.target, "days_left": status.days_left},
            fingerprint=_fp("cert_expiring", status.target, day),
        )
    return None


class CertWatch:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.results: list[CertStatus] = []
        self.last_check = 0.0

    def targets(self) -> list[str]:
        targets = list(self.svc.config.cert_watch)
        url = self.svc.config.server_agent_url
        if url:
            parsed = urlparse(url)
            if parsed.hostname and parsed.port:
                targets.append(f"{parsed.hostname}:{parsed.port}")
        return list(dict.fromkeys(targets))

    def check(self) -> list[CertStatus]:
        results = []
        for target in self.targets():
            parsed = parse_target(target)
            if parsed is None:
                results.append(CertStatus(target, error="only private addresses host:port allowed"))
                continue
            try:
                status = describe(target, fetch_certificate(*parsed))
            except (OSError, ssl.SSLError, ValueError) as exc:
                status = CertStatus(target, error=type(exc).__name__)
            results.append(status)
            finding = finding_for(status)
            if finding:
                self.svc.alerts.handle(finding)
        self.results = results
        self.last_check = time.time()
        return results

    def due(self) -> bool:
        return time.time() - self.last_check > CHECK_INTERVAL_S

    def status(self) -> list[dict[str, Any]]:
        return [r.__dict__ for r in self.results]
