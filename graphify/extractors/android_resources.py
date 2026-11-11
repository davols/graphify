"""Deterministic extraction of Android and Compose string resources."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from xml.parsers import expat

from graphify.extractors.base import _file_stem, _make_id, active_scan_root
from graphify.ids import decl_salt
from graphify.security import redact_secrets

_MAX_XML_BYTES = 2 * 1024 * 1024
_ESCAPE = re.compile(
    r"\\(?:u([dD][89aAbB][\da-fA-F]{2})\\u([dD][c-fC-F][\da-fA-F]{2})|u([\da-fA-F]{4})|(.))"
)


def _decode_escapes(value: str, *, android: bool) -> str:
    escapes = {"n": "\n", "t": "\t", "\\": "\\"}
    if android:
        escapes.update({"'": "'", '"': '"', "@": "@", "?": "?"})

    def decode(match: re.Match[str]) -> str:
        high, low, unit, character = match.groups()
        if high and low:
            return chr(0x10000 + ((int(high, 16) - 0xD800) << 10) + int(low, 16) - 0xDC00)
        if unit:
            codepoint = int(unit, 16)
            return match.group(0) if 0xD800 <= codepoint <= 0xDFFF else chr(codepoint)
        return escapes.get(character, match.group(0))

    result: list[str] = []
    quoted = False
    pending_space = False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and (match := _ESCAPE.match(value, index)):
            if pending_space and result:
                result.append(" ")
            pending_space = False
            result.append(decode(match))
            index = match.end()
            continue
        if android and character == '"':
            if not quoted and pending_space and result:
                result.append(" ")
            pending_space = False
            quoted = not quoted
        elif android and character in " \t\r\n" and not quoted:
            pending_space = True
        else:
            if pending_space and result:
                result.append(" ")
            pending_space = False
            result.append(character)
        index += 1
    return "".join(result)


def _failure(path: Path, reason: str) -> dict:
    print(f"  warning: Android resource XML {path}: {reason}", file=sys.stderr, flush=True)
    return {"nodes": [], "edges": [], "error": reason}


def extract_android_resources(path: Path) -> dict:
    """Extract searchable resource names and text without model calls."""
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_XML_BYTES + 1)
    except OSError as exc:
        return _failure(path, str(exc))
    if len(data) > _MAX_XML_BYTES:
        return _failure(path, "resource XML file too large")

    parser = expat.ParserCreate(namespace_separator="}")
    stack: list[str] = []
    records: list[tuple[str, str, int, str | None]] = []
    current: dict | None = None
    plural_name: str | None = None
    root_is_resources = False

    def reject_doctype(*_args) -> None:
        raise ValueError("refusing XML with DOCTYPE or ENTITY declaration")

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal current, plural_name, root_is_resources
        tag = name.rsplit("}", 1)[-1]
        stack.append(tag)
        if len(stack) == 1:
            root_is_resources = tag == "resources"
        if (
            root_is_resources
            and attrs.get("name")
            and (
                stack == ["resources", "string"]
                or (stack == ["resources", "item"] and attrs.get("type") == "string")
            )
        ):
            current = {
                "name": attrs["name"],
                "line": parser.CurrentLineNumber,
                "text": [],
                "quantity": None,
            }
        elif root_is_resources and stack == ["resources", "plurals"]:
            plural_name = attrs.get("name")
        elif (
            root_is_resources
            and stack == ["resources", "plurals", "item"]
            and plural_name
            and attrs.get("quantity")
        ):
            current = {
                "name": plural_name,
                "line": parser.CurrentLineNumber,
                "text": [],
                "quantity": attrs["quantity"],
            }

    def text(data: str) -> None:
        if current is not None:
            current["text"].append(data)

    def end(_name: str) -> None:
        nonlocal current, plural_name
        if current is not None and stack in (
            ["resources", "string"],
            ["resources", "item"],
            ["resources", "plurals", "item"],
        ):
            records.append(
                (current["name"], "".join(current["text"]), current["line"], current["quantity"])
            )
            current = None
        if stack == ["resources", "plurals"]:
            plural_name = None
        stack.pop()

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.EntityDeclHandler = reject_doctype
    parser.ExternalEntityRefHandler = lambda *_args: 0
    parser.StartElementHandler = start
    parser.CharacterDataHandler = text
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        return _failure(path, f"malformed XML: {exc}")
    except ValueError as exc:
        return _failure(path, str(exc))
    if not root_is_resources:
        return {"nodes": [], "edges": [], "skipped": "not an Android resources document"}

    source = str(path)
    stem = _file_stem(path)
    root = active_scan_root()
    absolute_path = Path(os.path.abspath(path))
    relative_path = absolute_path
    if root:
        try:
            relative_path = absolute_path.relative_to(root)
        except ValueError:
            for ancestor in absolute_path.parents:
                try:
                    if ancestor.resolve() == root:
                        relative_path = absolute_path.relative_to(ancestor)
                        break
                except (OSError, RuntimeError):
                    continue
    path_salt = decl_salt(relative_path.as_posix())
    file_id = _make_id(source, "android_resource_file", path_salt)
    nodes = [
        {
            "id": file_id,
            "label": path.name,
            "file_type": "code",
            "source_file": source,
            "source_location": "L1",
        }
    ]
    edges = []
    seen_ids: set[str] = set()
    for name, value, line, quantity in records:
        kind = "plurals" if quantity else "string"
        spelling = "\0".join((kind, name, quantity or ""))
        safe_name = redact_secrets(name)
        safe_quantity = redact_secrets(quantity) if quantity else None
        safe_value = redact_secrets(
            _decode_escapes(value, android=path.parent.parent.name == "res")
        )
        searchable_value = safe_value.replace("\n", " ").replace("\t", " ").replace("\r", " ")
        visible_value = safe_value.replace("\n", r"\n").replace("\t", r"\t").replace("\r", r"\r")
        resource_id = _make_id(
            stem, kind, safe_name, safe_quantity or "", path_salt, decl_salt(spelling)
        )
        if resource_id in seen_ids:
            return _failure(path, f"duplicate resource {safe_name!r}")
        seen_ids.add(resource_id)
        quantity_label = f" ({safe_quantity})" if safe_quantity else ""
        node = {
            "id": resource_id,
            "label": f"{safe_name}{quantity_label}: {searchable_value}",
            "file_type": "code",
            "source_file": source,
            "source_location": f"L{line}",
            "metadata": {
                "resource_name": safe_name,
                "qualifier": path.parent.name,
                "quantity": safe_quantity,
                "resource_type": kind,
            },
        }
        if visible_value != searchable_value:
            node["attributes"] = {"formatted_value": visible_value}
        nodes.append(node)
        edges.append(
            {
                "source": file_id,
                "target": resource_id,
                "relation": "contains",
                "confidence": "EXTRACTED",
                "source_file": source,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )
    return {"nodes": nodes, "edges": edges}
