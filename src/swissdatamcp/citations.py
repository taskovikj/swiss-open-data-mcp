"""Citation helpers for Swiss open data metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from swissdatamcp.models import DatasetSummary, ResourceSummary


def dataset_citation(dataset: DatasetSummary, resource: ResourceSummary | None = None) -> dict[str, str | None]:
    """Build a compact citation object for a dataset/resource."""

    return {
        "title": dataset.title or dataset.name,
        "publisher": dataset.organization,
        "dataset_id": dataset.id,
        "dataset_url": dataset.url,
        "license": dataset.license,
        "resource_id": resource.id if resource else None,
        "resource_name": resource.name if resource else None,
        "resource_url": resource.best_url if resource else None,
        "accessed_at": datetime.now(UTC).date().isoformat(),
    }

