"""opendata.swiss CKAN catalog connector."""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from typing import Any

import httpx

from swissdatamcp.config import Settings
from swissdatamcp.models import DatasetSummary, ResourceSummary
from swissdatamcp.network import RETRYABLE_STATUS_CODES, retry_delay
from swissdatamcp.text import clean_text, ensure_list, first_present, pick_localized, truncate


class CatalogError(RuntimeError):
    """Raised when the Swiss open-data catalog cannot satisfy a request."""


class OpenDataSwissClient:
    """Small CKAN Action API client for opendata.swiss metadata."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.headers = {"User-Agent": settings.user_agent}
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    async def _get(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.settings.ckan_base_url}/{action}"
        key = repr((action, sorted((params or {}).items())))
        cached = self._cache.get(key)
        if action != "status_show" and cached and time.monotonic() < cached[0]:
            self._cache.move_to_end(key)
            return copy.deepcopy(cached[1])
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            for attempt in range(3):
                try:
                    response = await client.get(url, params=params or {})
                    if response.status_code in RETRYABLE_STATUS_CODES and attempt < 2:
                        await asyncio.sleep(
                            retry_delay(attempt, response.headers.get("retry-after"))
                        )
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    break
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise CatalogError(
                            "Catalog connection failed after three attempts."
                        ) from exc
                    await asyncio.sleep(retry_delay(attempt))
                except (httpx.HTTPStatusError, ValueError) as exc:
                    raise CatalogError(
                        f"CKAN action {action} returned an invalid response."
                    ) from exc
        if not isinstance(payload, dict):
            raise CatalogError("Catalog returned a non-object response.")
        if not payload.get("success", False):
            raise CatalogError(f"CKAN action {action} failed: {payload.get('error')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CatalogError("Catalog returned a missing or invalid result.")
        if action != "status_show":
            self._cache[key] = (time.monotonic() + 300, copy.deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return result

    async def status(self) -> dict[str, Any]:
        """Return CKAN platform status."""

        return await self._get("status_show")

    async def search(
        self,
        query: str,
        rows: int = 10,
        start: int = 0,
        organization: str | None = None,
        language: str | None = None,
        groups: str | None = None,
    ) -> dict[str, Any]:
        """Search opendata.swiss packages via CKAN package_search."""

        fq_parts: list[str] = []
        if organization:
            fq_parts.append(f"organization:{organization}")
        if language:
            fq_parts.append(f"language:{language}")
        if groups:
            fq_parts.append(f"groups:{groups}")

        params: dict[str, Any] = {
            "q": query,
            "rows": max(1, min(rows, 50)),
            "start": max(0, start),
        }
        if fq_parts:
            params["fq"] = " ".join(fq_parts)

        result = await self._get("package_search", params)
        datasets = [
            normalize_dataset(item, preferred_language=language or "en")
            for item in result.get("results", [])
        ]
        count = result.get("count", len(datasets))
        actual_start = params["start"]
        next_start = actual_start + len(datasets)
        return {
            "query": query,
            "count": count,
            "start": actual_start,
            "rows": params["rows"],
            "returned_count": len(datasets),
            "has_more": bool(datasets) and next_start < count,
            "next_start": next_start if datasets and next_start < count else None,
            "datasets": [
                dataset.model_dump(by_alias=True, exclude={"raw"}) for dataset in datasets
            ],
        }

    async def show_dataset(self, dataset_id: str, language: str = "en") -> DatasetSummary:
        """Fetch a full CKAN package by id/name."""

        result = await self._get("package_show", {"id": dataset_id})
        return normalize_dataset(result, preferred_language=language, include_raw=True)


def normalize_dataset(
    item: dict[str, Any],
    preferred_language: str = "en",
    include_raw: bool = False,
) -> DatasetSummary:
    """Normalize a CKAN package into a compact schema."""

    organization = item.get("organization") or {}
    org_name = first_present(
        [
            pick_localized(organization.get("title"), preferred_language),
            pick_localized(organization.get("display_name"), preferred_language),
            organization.get("name"),
        ]
    )
    title = first_present(
        [
            pick_localized(item.get("title"), preferred_language),
            pick_localized(item.get("display_name"), preferred_language),
            item.get("name"),
        ]
    )
    description = first_present(
        [
            pick_localized(item.get("description"), preferred_language),
            pick_localized(item.get("notes"), preferred_language),
        ]
    )
    resources = [
        normalize_resource(resource, preferred_language) for resource in item.get("resources", [])
    ]
    keywords = ensure_list(item.get("keywords"))
    tags = ensure_list(item.get("tags"))
    languages = ensure_list(item.get("language"))

    return DatasetSummary(
        id=str(item.get("id") or item.get("name")),
        name=str(item.get("name") or item.get("id")),
        title=truncate(title, 300),
        description=truncate(description, 1200),
        organization=org_name,
        url=item.get("url") or f"https://opendata.swiss/en/dataset/{item.get('name')}",
        license=clean_text(str(item.get("license_id") or item.get("license_title") or "")) or None,
        language=languages,
        keywords=keywords,
        tags=tags,
        resources=resources,
        score=float(item["score"]) if item.get("score") is not None else None,
        raw=item if include_raw else {},
    )


def normalize_resource(resource: dict[str, Any], preferred_language: str = "en") -> ResourceSummary:
    """Normalize a CKAN resource/distribution record."""

    return ResourceSummary(
        id=resource.get("id"),
        name=first_present(
            [
                pick_localized(resource.get("name"), preferred_language),
                pick_localized(resource.get("title"), preferred_language),
            ]
        ),
        description=truncate(pick_localized(resource.get("description"), preferred_language), 800),
        format=resource.get("format"),
        **{
            "media-type": resource.get("media-type")
            or resource.get("mimetype")
            or resource.get("mimetype_inner")
        },
        download_url=resource.get("download_url") or resource.get("downloadURL"),
        access_url=resource.get("access_url") or resource.get("accessURL"),
        url=resource.get("url"),
        rights=pick_localized(resource.get("rights"), preferred_language),
        issued=resource.get("issued") or resource.get("created"),
        modified=resource.get("modified") or resource.get("last_modified"),
    )
