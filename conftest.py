"""Cross-suite test infrastructure."""

from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import sys
import urllib.request
from collections.abc import Iterator

import certifi
import httpx._utils
import pytest
from loguru import logger


def _fake_proxy_dns_active() -> bool:
    """Detect macOS proxy clients that expose public hosts through fake IP ranges."""
    try:
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(
                "example.com",
                None,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError):
        return False
    return any(
        address in ipaddress.ip_network("198.18.0.0/15")
        or address in ipaddress.ip_network("fd00::/8")
        for address in addresses
    )


_FAKE_PROXY_DNS_ACTIVE = _fake_proxy_dns_active()


@pytest.fixture(autouse=True)
def _isolate_host_proxy_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep macOS/Windows system proxy settings out of deterministic tests.

    HTTPX and urllib otherwise fall back to host-level proxy configuration even
    after a test clears proxy environment variables. Some proxy clients also
    return synthetic 198.18/15 or ULA addresses for public documentation hosts,
    causing SSRF tests to fail before their mocked transports are exercised.
    """
    monkeypatch.setattr(
        httpx._utils,
        "getproxies",
        urllib.request.getproxies_environment,
    )
    monkeypatch.setattr(
        "nanobot.security.network.getproxies",
        urllib.request.getproxies_environment,
    )
    monkeypatch.setattr(
        "nanobot.security.network.proxy_bypass",
        urllib.request.proxy_bypass_environment,
    )

    if not _FAKE_PROXY_DNS_ACTIVE:
        return

    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ):
        results = original_getaddrinfo(host, port, family, type, proto, flags)
        hostname = host.decode() if isinstance(host, bytes) else (host or "")
        normalized = hostname.rstrip(".").lower()
        if not (
            normalized in {"example.com", "example.org", "www.google.com"}
            or normalized.endswith((".example.com", ".example.org"))
        ):
            return results

        normalized_results = []
        for result in results:
            address = ipaddress.ip_address(result[4][0])
            if address in ipaddress.ip_network("198.18.0.0/15"):
                sockaddr = ("93.184.216.34", *result[4][1:])
                normalized_results.append((*result[:4], sockaddr))
            elif address in ipaddress.ip_network("fd00::/8"):
                sockaddr = ("2606:2800:220:1:248:1893:25c8:1946", *result[4][1:])
                normalized_results.append((*result[:4], sockaddr))
            else:
                normalized_results.append(result)
        return normalized_results

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def _isolate_nanobot_log_activation() -> Iterator[None]:
    """Keep CLI log settings from leaking into later tests in the same process."""
    logger.enable("nanobot")
    try:
        yield
    finally:
        logger.enable("nanobot")


@pytest.fixture(scope="session", autouse=True)
def _use_windows_system_ca_for_default_http_clients() -> Iterator[None]:
    """Avoid reparsing certifi's CA bundle for every offline HTTP client.

    Loading certifi takes roughly 0.7 seconds per client on Windows. The test
    suite constructs hundreds of clients while mocking their I/O. System roots
    preserve certificate verification for accidental local requests; explicit
    ``cafile``, ``capath``, and ``cadata`` arguments still use the real loader.
    """
    if sys.platform != "win32":
        yield
        return

    original = ssl.create_default_context
    certifi_path = os.path.normcase(os.path.abspath(certifi.where()))

    def create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        requested_path = os.path.normcase(os.path.abspath(cafile)) if cafile else None
        if requested_path == certifi_path and capath is None and cadata is None:
            return original(purpose)
        return original(
            purpose,
            cafile=cafile,
            capath=capath,
            cadata=cadata,
        )

    ssl.create_default_context = create_default_context
    try:
        yield
    finally:
        ssl.create_default_context = original
