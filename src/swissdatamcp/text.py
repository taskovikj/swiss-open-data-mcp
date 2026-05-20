"""Text utilities for SwissDataMCP."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any


def pick_localized(value: Any, preferred_language: str = "en") -> str | None:
    """Extract a readable string from CKAN localized dictionaries/lists."""

    if value is None:
        return None
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        for key in (preferred_language, "en", "de", "fr", "it"):
            if key in value and value[key]:
                return clean_text(str(value[key]))
        for item in value.values():
            if item:
                return clean_text(str(item))
    if isinstance(value, list):
        parts = [pick_localized(item, preferred_language) for item in value]
        return ", ".join(part for part in parts if part)
    return clean_text(str(value))


def clean_text(value: str | None) -> str | None:
    """Normalize whitespace and strip simple HTML from metadata."""

    if value is None:
        return None
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def ensure_list(value: Any) -> list[str]:
    """Convert CKAN mixed metadata values into a list of strings."""

    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                label = pick_localized(item.get("display_name") or item.get("name") or item)
            else:
                label = pick_localized(item)
            if label:
                result.append(label)
        return result
    if isinstance(value, dict):
        picked = pick_localized(value)
        return [picked] if picked else []
    picked = pick_localized(value)
    return [picked] if picked else []


def truncate(value: str | None, max_chars: int = 500) -> str | None:
    """Return a readable shortened text block."""

    if value is None or len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def first_present(values: Iterable[Any]) -> Any | None:
    """Return the first non-empty value."""

    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None

