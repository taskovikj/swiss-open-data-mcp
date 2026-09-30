import httpx
import pytest

from swissdatamcp import catalog as module
from swissdatamcp.catalog import CatalogError, OpenDataSwissClient


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


async def test_catalog_retries_then_caches_without_mutable_aliases(settings, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"retry-after": "0"})
        return httpx.Response(
            200, json={"success": True, "result": {"count": 1, "results": [{"id": "a"}]}}
        )

    mock_client(monkeypatch, handler)
    client = OpenDataSwissClient(settings)
    first = await client._get("package_search", {"q": "population"})
    first["results"].clear()
    second = await client._get("package_search", {"q": "population"})
    assert len(calls) == 2
    assert second["results"] == [{"id": "a"}]


@pytest.mark.parametrize(
    "payload", [[], {"success": False}, {"success": True}, {"success": True, "result": []}]
)
async def test_bad_catalog_payloads_are_actionable(settings, monkeypatch, payload):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(CatalogError):
        await OpenDataSwissClient(settings).status()


async def test_search_pagination_and_language(settings, monkeypatch):
    client = OpenDataSwissClient(settings)

    async def result(action, params):
        return {
            "count": 60,
            "results": [{"id": "a", "title": {"de": "Bevoelkerung", "en": "Population"}}],
        }

    monkeypatch.setattr(client, "_get", result)
    page = await client.search("population", rows=100, start=-1, language="de")
    assert page["rows"] == 50 and page["start"] == 0
    assert page["next_start"] == 1 and page["has_more"]
    assert page["datasets"][0]["title"] == "Bevoelkerung"
