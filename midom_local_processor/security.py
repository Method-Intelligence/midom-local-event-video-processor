from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .constants import SHARED_DEV_NETWORK


def _is_loopback_host(hostname: str | None) -> bool:
    hostname = str(hostname or "").strip().lower().rstrip(".")
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_private_lan_address(address: ipaddress._BaseAddress) -> bool:
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        return False
    return bool(address.is_private or address.is_link_local or address in SHARED_DEV_NETWORK)


def _is_private_lan_dev_host(hostname: str | None) -> bool:
    hostname = str(hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    try:
        return _is_private_lan_address(ipaddress.ip_address(hostname))
    except ValueError:
        pass
    if hostname.endswith(".local") or "." not in hostname:
        return True
    try:
        resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = []
    for info in resolved:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            addresses.append(ipaddress.ip_address(str(sockaddr[0])))
        except ValueError:
            continue
    return bool(addresses) and all(_is_private_lan_address(address) for address in addresses)


def normalize_api_base_url(api_base_url: str, *, allow_local_http: bool = False, allow_lan_http: bool = False) -> str:
    api_base_url = str(api_base_url or "").strip().rstrip("/")
    if not api_base_url:
        raise ValueError("API base URL is required.")
    parsed = urlparse(api_base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("API base URL must be a complete http(s) URL.")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("API base URL must not include query strings, fragments, or URL parameters.")
    if parsed.username or parsed.password:
        raise ValueError("API base URL must not include credentials.")
    if parsed.scheme == "https":
        return api_base_url
    is_loopback = _is_loopback_host(parsed.hostname)
    is_private_lan = _is_private_lan_dev_host(parsed.hostname)
    if (allow_local_http and is_loopback) or (allow_lan_http and is_private_lan):
        return api_base_url
    raise ValueError(
        "HTTPS is required. HTTP is allowed only for explicit localhost or private LAN/tailnet development. "
        f"host={parsed.hostname!r} allow_local_http={allow_local_http} allow_lan_http={allow_lan_http} "
        f"is_loopback={is_loopback} is_private_lan={is_private_lan}."
    )
