"""Shared labels and Swiss code helpers."""

from __future__ import annotations

import re


COMMON_COLUMN_LABELS = {
    "year": "Year",
    "jahr": "Year",
    "canton": "Canton",
    "kanton": "Canton",
    "canton_code": "Canton code",
    "kt": "Canton",
    "age_mother": "Mother's age band",
    "sex_child": "Child sex",
    "obs_value": "Observed value",
    "record_count": "Records",
    "lat": "Latitude",
    "latitude": "Latitude",
    "lon": "Longitude",
    "lng": "Longitude",
    "longitude": "Longitude",
    "left_value": "Left metric",
    "right_value": "Right metric",
    "join_value": "Join value",
    "metric_x": "Indicator",
    "metric_y": "Indicator",
    "correlation": "Pearson r",
}


AGE_MOTHER_LABELS = {
    "_T": "Total",
    "Y10T14": "10 to 14 years",
    "Y15T19": "15 to 19 years",
    "Y20T24": "20 to 24 years",
    "Y25T29": "25 to 29 years",
    "Y30T34": "30 to 34 years",
    "Y35T39": "35 to 39 years",
    "Y40T44": "40 to 44 years",
    "Y45T49": "45 to 49 years",
    "Y50T54": "50 to 54 years",
    "Y55T59": "55 to 59 years",
    "Y60T64": "60 to 64 years",
    "Y65T69": "65 to 69 years",
}


SEX_CHILD_LABELS = {
    "T": "Total",
    "1": "Male",
    "2": "Female",
}


def normalize_text(value: str) -> str:
    """Normalize text for simple Swiss name lookups."""

    normalized = value.lower()
    normalized = normalized.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    normalized = normalized.replace("\u00e9", "e").replace("\u00e8", "e").replace("\u00ea", "e")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


CANTON_BY_CODE = {
    "1": ("ZH", "Zurich"),
    "2": ("BE", "Bern"),
    "3": ("LU", "Luzern"),
    "4": ("UR", "Uri"),
    "5": ("SZ", "Schwyz"),
    "6": ("OW", "Obwalden"),
    "7": ("NW", "Nidwalden"),
    "8": ("GL", "Glarus"),
    "9": ("ZG", "Zug"),
    "10": ("FR", "Fribourg"),
    "11": ("SO", "Solothurn"),
    "12": ("BS", "Basel-Stadt"),
    "13": ("BL", "Basel-Landschaft"),
    "14": ("SH", "Schaffhausen"),
    "15": ("AR", "Appenzell Ausserrhoden"),
    "16": ("AI", "Appenzell Innerrhoden"),
    "17": ("SG", "St. Gallen"),
    "18": ("GR", "Graubunden"),
    "19": ("AG", "Aargau"),
    "20": ("TG", "Thurgau"),
    "21": ("TI", "Ticino"),
    "22": ("VD", "Vaud"),
    "23": ("VS", "Valais"),
    "24": ("NE", "Neuchatel"),
    "25": ("GE", "Geneva"),
    "26": ("JU", "Jura"),
}

CANTON_BY_ABBR = {abbr: (code, name) for code, (abbr, name) in CANTON_BY_CODE.items()}
CANTON_BY_NAME = {normalize_text(name): (code, abbr, name) for code, (abbr, name) in CANTON_BY_CODE.items()}
CANTON_BY_NAME.update(
    {
        "zurich": ("1", "ZH", "Zurich"),
        "zuerich": ("1", "ZH", "Zurich"),
        "bern": ("2", "BE", "Bern"),
        "lucerne": ("3", "LU", "Luzern"),
        "freiburg": ("10", "FR", "Fribourg"),
        "grisons": ("18", "GR", "Graubunden"),
        "graubuenden": ("18", "GR", "Graubunden"),
        "tessin": ("21", "TI", "Ticino"),
        "waadt": ("22", "VD", "Vaud"),
        "wallis": ("23", "VS", "Valais"),
        "geneve": ("25", "GE", "Geneva"),
    }
)


def normalize_label_key(value: str) -> str:
    """Normalize a column name for label lookup."""

    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def readable_column_label(column: str) -> str:
    """Return a human-readable label for a column name."""

    key = normalize_label_key(column)
    if key in COMMON_COLUMN_LABELS:
        return COMMON_COLUMN_LABELS[key]
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", column)
    words = words.replace("_", " ").strip()
    if not words:
        return column
    return " ".join(
        word.upper() if word.upper() in {"UID", "ID", "URL", "LV95", "CH"} else word.capitalize()
        for word in words.split()
    )


def automatic_column_labels(columns: list[str | None]) -> dict[str, str]:
    """Return labels for columns with known or readable names."""

    return {column: readable_column_label(column) for column in columns if column}


def automatic_value_labels(columns: list[str | None]) -> dict[str, dict[str, str]]:
    """Return value label maps for known Swiss statistical code columns."""

    labels: dict[str, dict[str, str]] = {}
    for column in columns:
        if not column:
            continue
        key = normalize_label_key(column)
        if key in {"age_mother", "mother_age", "alter_mutter"}:
            labels[column] = dict(AGE_MOTHER_LABELS)
        elif key in {"sex_child", "child_sex", "geschlecht_kind"}:
            labels[column] = dict(SEX_CHILD_LABELS)
        elif key in {"canton", "kanton", "canton_code", "kt"}:
            labels[column] = canton_value_labels()
    return labels


def canton_value_labels() -> dict[str, str]:
    """Return labels for common canton code representations."""

    labels = {"CH": "Switzerland", "_T": "Total"}
    for code, (abbr, name) in CANTON_BY_CODE.items():
        labels[code] = name
        labels[code.zfill(2)] = name
        labels[abbr] = name
    return labels


def normalize_canton_value(value: object) -> dict[str, str | None]:
    """Normalize a canton code, abbreviation, or name."""

    if value is None:
        return {"canton_code": None, "canton_abbreviation": None, "canton_name": None}
    text = str(value).strip()
    if not text:
        return {"canton_code": None, "canton_abbreviation": None, "canton_name": None}
    upper = text.upper()
    if upper == "CH":
        return {"canton_code": "CH", "canton_abbreviation": "CH", "canton_name": "Switzerland"}
    if upper in CANTON_BY_ABBR:
        code, name = CANTON_BY_ABBR[upper]
        return {"canton_code": code, "canton_abbreviation": upper, "canton_name": name}
    numeric = upper.lstrip("0")
    if numeric in CANTON_BY_CODE:
        abbr, name = CANTON_BY_CODE[numeric]
        return {"canton_code": numeric, "canton_abbreviation": abbr, "canton_name": name}
    normalized = normalize_text(text)
    if normalized in CANTON_BY_NAME:
        code, abbr, name = CANTON_BY_NAME[normalized]
        return {"canton_code": code, "canton_abbreviation": abbr, "canton_name": name}
    return {"canton_code": None, "canton_abbreviation": None, "canton_name": None}
