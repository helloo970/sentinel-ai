"""Network boundaries: only private addresses per RFC 1918."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

RFC1918 = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)

# Largest allowed subnet for scans (1024 addresses)
MAX_PREFIX = 22


def is_rfc1918(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if not isinstance(ip, ipaddress.IPv4Address):
        return False
    return any(ip in net for net in RFC1918)


def validate_home_subnet(value: str) -> ipaddress.IPv4Network:
    net = ipaddress.ip_network(value, strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        raise ValueError("only IPv4 subnets are supported")
    if not any(net.subnet_of(private) for private in RFC1918):
        raise ValueError("subnet must be inside RFC 1918 private ranges")
    if net.prefixlen < MAX_PREFIX:
        raise ValueError(f"subnet too large, use /{MAX_PREFIX} or smaller")
    return net


def ip_in_subnet(address: str, subnet: ipaddress.IPv4Network | None) -> bool:
    if not is_rfc1918(address):
        return False
    if subnet is None:
        return True
    return ipaddress.IPv4Address(address) in subnet


def resolve_private(host: str) -> bool:
    """True if all resolved addresses of the host are private (RFC 1918)."""
    if is_rfc1918(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError:
        return False
    addresses = {str(info[4][0]) for info in infos}
    return bool(addresses) and all(is_rfc1918(a) for a in addresses)


def url_is_private(url: str, *, require_https: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ({"https"} if require_https else {"http", "https"}):
        return False
    if not parsed.hostname:
        return False
    return resolve_private(parsed.hostname)
