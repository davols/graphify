"""Identify Android and Compose values resource files by their paths."""

from __future__ import annotations

from pathlib import Path


def is_android_resource_xml(path: Path) -> bool:
    values = path.parent.name
    return (
        path.suffix.lower() == ".xml"
        and (values == "values" or values.startswith("values-"))
        and path.parent.parent.name in {"res", "composeResources"}
    )
