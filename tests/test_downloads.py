import asyncio
import socket

import httpx
import pytest

from swissdatamcp import store as store_module
from swissdatamcp.network import validate_public_url
from swissdatamcp.store import StoreError


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a.csv",
        "https://user:pass@example.com/a.csv",
        "http://localhost/a.csv",
        "http://127.0.0.1/a.csv",
        "http://10.0.0.1/a.csv",
        "http://169.254.169.254/a.csv",
        "http://[::1]/a.csv",
        "http://[::ffff:127.0.0.1]/a.csv",
    ],
)
async def test_public_url_validation_rejects_private_addresses(url, monkeypatch):
    async def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        await validate_public_url(url)


async def test_public_url_rejects_mixed_dns_answers(monkeypatch):
    async def resolve(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ["8.8.8.8", "192.168.1.1"]
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        await validate_public_url("https://example.com/data.csv")


def mock_network(monkeypatch, handler):
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(
        store_module.httpx,
        "AsyncClient",
        lambda **kwargs: client_factory(transport=httpx.MockTransport(handler), **kwargs),
    )


class InterruptedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"year,value\n"
        raise httpx.ReadError("interrupted")


class CancelledStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"year,value\n"
        raise asyncio.CancelledError()


async def test_cancellation_cleans_up_download(data_store, monkeypatch):
    async def allow(url):
        pass

    monkeypatch.setattr(store_module, "validate_public_url", allow)
    mock_network(monkeypatch, lambda request: httpx.Response(200, stream=CancelledStream()))
    with pytest.raises(asyncio.CancelledError):
        await data_store.download_resource("https://example.com/data.csv")
    assert not list(data_store.settings.downloads_dir.iterdir())


@pytest.mark.parametrize("failure", ["interrupted", "oversize", "empty"])
async def test_failed_downloads_leave_no_partial_cache(data_store, monkeypatch, failure):
    async def allow(url):
        pass

    monkeypatch.setattr(store_module, "validate_public_url", allow)
    responses = {
        "interrupted": lambda: httpx.Response(200, stream=InterruptedStream()),
        "oversize": lambda: httpx.Response(200, headers={"content-length": str(100 * 1024 * 1024)}),
        "empty": lambda: httpx.Response(200, content=b""),
    }
    mock_network(monkeypatch, lambda request: responses[failure]())
    with pytest.raises(StoreError):
        await data_store.download_resource("https://example.com/data.csv")
    assert list(data_store.settings.downloads_dir.iterdir()) == []


async def test_redirect_is_validated_before_request(data_store, monkeypatch):
    requests = []

    async def validate(url):
        if "127.0.0.1" in url:
            raise ValueError("private redirect")

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    monkeypatch.setattr(store_module, "validate_public_url", validate)
    mock_network(monkeypatch, handler)
    with pytest.raises(StoreError, match="private redirect"):
        await data_store.download_resource("https://example.com/data.csv")
    assert requests == ["https://example.com/data.csv"]


async def test_successful_download_cache_hit(data_store, monkeypatch):
    calls = []

    async def allow(url):
        pass

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"year,value\n2020,2\n")

    monkeypatch.setattr(store_module, "validate_public_url", allow)
    mock_network(monkeypatch, handler)
    first = await data_store.download_resource("https://example.com/data.csv")
    second = await data_store.download_resource("https://example.com/data.csv")
    assert first == second
    assert len(calls) == 1
    assert not list(data_store.settings.downloads_dir.glob("*.part"))
    first.write_bytes(b"truncated")
    await data_store.download_resource("https://example.com/data.csv")
    assert len(calls) == 2
    assert first.read_bytes() == b"year,value\n2020,2\n"
    await data_store.download_resource("https://example.com/data.csv", refresh=True)
    assert len(calls) == 3
