"""Public download URL checks and bounded retry policy."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


async def validate_public_url(url: str) -> None:
    """Reject credentials and non-public destinations, including redirect targets.

    This is a preflight check, not a replacement for network egress controls:
    the HTTP transport resolves the hostname again when it connects.
    """

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid resource URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http(s) resource URLs are supported.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Resource URLs must not contain credentials.")
    host = parsed.hostname.rstrip(".")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ValueError("Resource URLs must point to public internet addresses.")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError("Could not resolve resource hostname.") from exc
    if not addresses:
        raise ValueError("Could not resolve resource hostname.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("Resource URLs must point to public internet addresses.")


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Use short exponential backoff, respecting bounded numeric Retry-After."""

    if retry_after:
        try:
            return min(5.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return min(2.0, 0.25 * 2**attempt)
