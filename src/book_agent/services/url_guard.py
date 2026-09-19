"""Outbound URL checks for user-supplied provider endpoints (SSRF, R1)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """The URL points at a scheme or network the server must not call."""


Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve host {host!r}") from exc


def ensure_public_http_url(url: str, *, resolver: Resolver | None = None) -> None:
    """Require http(s) and a host whose every address is public (no loopback, private, link-local, metadata)."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("base_url must be an http(s) URL with a host")
    addresses = (resolver or _resolve)(parsed.hostname)
    if not addresses:
        raise UnsafeUrlError(f"host {parsed.hostname!r} has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrlError(
                f"base_url host {parsed.hostname!r} resolves to a non-public address ({ip}); "
                "set BOOK_AGENT_PROVIDER_ALLOW_PRIVATE_HOSTS=true for a self-hosted model on a private network"
            )
