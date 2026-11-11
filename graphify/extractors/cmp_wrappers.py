"""Resolve Compose resource wrappers from their Kotlin declarations."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from graphify.extractors.base import _make_id, _read_source_bytes
from graphify.ids import decl_salt
from graphify.security import redact_secrets

_KOTLIN_KEYWORDS = frozenset({
    "as", "break", "class", "continue", "do", "else", "false", "for", "fun",
    "if", "in", "interface", "is", "null", "object", "package", "return",
    "super", "this", "throw", "true", "try", "typealias", "typeof", "val",
    "var", "when", "while",
})


def _kotlin_name(name: str) -> str:
    return f"`{name}`" if name in _KOTLIN_KEYWORDS or not name.isidentifier() else name


def refresh_cmp_paths(changed: list[Path], live: list[Path]) -> list[Path]:
    """Re-extract a CMP source set when one of its XML or Kotlin files changes."""
    live = [Path(os.path.abspath(path)) for path in live]
    changed = [Path(os.path.abspath(path)) for path in changed]
    scopes = {
        path.parent.parent.parent
        for path in [*live, *changed]
        if path.suffix == ".xml" and path.parent.parent.name == "composeResources"
    }
    affected = {
        scope for scope in scopes
        if any(path.is_relative_to(scope) and path.suffix in (".kt", ".xml") for path in changed)
    }
    return [
        path for path in live
        if any(path.is_relative_to(scope) for scope in affected)
        and (path.suffix == ".kt" or (path.suffix == ".xml" and path.parent.parent.name == "composeResources"))
    ]


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8").strip().strip("`")


def _segments(node, source: bytes) -> list[str] | None:
    if node.type in ("identifier", "simple_identifier"):
        return [_text(node, source)]
    if node.type != "navigation_expression":
        return None
    children = node.named_children
    if len(children) != 2:
        return None
    left = _segments(children[0], source)
    right = _segments(children[1], source)
    return left + right if left is not None and right is not None else None


def _is_public(node, source: bytes) -> bool:
    for child in node.named_children:
        if child.type != "modifiers":
            continue
        for modifier in child.named_children:
            if modifier.type == "visibility_modifier" and _text(modifier, source) != "public":
                return False
    return True


def _declarations(path: Path, parser) -> tuple[str, list[tuple[str, str, list[str], int]]]:
    try:
        source = _read_source_bytes(path, warn=False)
        root = parser.parse(source).root_node
    except (OSError, ValueError):
        return "", []
    package = ""
    declarations = []
    for node in root.named_children:
        if node.type == "package_header":
            name = next((child for child in node.named_children if child.type in ("qualified_identifier", "identifier")), None)
            if name:
                package = _text(name, source)
        if node.type != "object_declaration" or not _is_public(node, source):
            continue
        name = node.child_by_field_name("name")
        body = next((child for child in node.named_children if child.type == "class_body"), None)
        if not name or not body:
            continue
        for prop in body.named_children:
            if prop.type != "property_declaration" or not _is_public(prop, source):
                continue
            variable = next((child for child in prop.named_children if child.type == "variable_declaration"), None)
            if not variable or not variable.named_children:
                continue
            identifier = variable.named_children[0]
            # Only direct object aliases and direct Res.string/plurals references.
            expressions = [child for child in prop.named_children if child != variable and child.type in ("identifier", "simple_identifier", "navigation_expression")]
            if len(expressions) != 1:
                continue
            segments = _segments(expressions[0], source)
            if segments:
                declarations.append((_text(name, source), _text(identifier, source), segments, prop.start_point[0] + 1))
    return package, declarations


def add_cmp_wrapper_links(paths: list[Path], nodes: list[dict], edges: list[dict], root: Path) -> None:
    """Link local Compose XML values to explicitly declared wrapper accessors."""
    resources = defaultdict(list)
    for node in nodes:
        metadata = node.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        source = Path(node.get("source_file", ""))
        if not source.is_absolute():
            source = root / source
        if source.parent.parent.name != "composeResources" or not metadata.get("resource_name"):
            continue
        scope = source.parent.parent.parent
        resources[(scope, metadata["resource_type"], metadata["resource_name"])].append(node)
    if not resources:
        return
    try:
        import tree_sitter_kotlin
        from tree_sitter import Language, Parser
        parser = Parser(Language(tree_sitter_kotlin.language()))
    except (ImportError, TypeError):
        return
    aliases = defaultdict(list)
    wrappers = []
    scopes = {key[0] for key in resources}
    for path in paths:
        if path.suffix != ".kt":
            continue
        path = Path(os.path.abspath(path))
        for scope in scopes:
            if not path.is_relative_to(scope):
                continue
            package, declarations = _declarations(path, parser)
            for obj, prop, segments, line in declarations:
                if len(segments) == 1:
                    aliases[(scope, package, segments[0])].append((obj, prop, path, line))
                elif len(segments) == 3 and segments[0] == "Res" and segments[1] in ("string", "plurals"):
                    wrappers.append((scope, package, obj, prop, segments[1], segments[2], path, line))
    counts = defaultdict(int)
    for scope, package, obj, prop, kind, name, path, line in wrappers:
        counts[(scope, package, obj, prop)] += 1
    for scope, package, obj, prop, kind, name, path, line in wrappers:
        choices = aliases.get((scope, package, obj), [])
        if len(choices) != 1 or counts[(scope, package, obj, prop)] != 1:
            continue
        candidates = resources.get((scope, kind, name), [])
        qualifier_counts = defaultdict(int)
        for candidate in candidates:
            metadata = candidate["metadata"]
            qualifier_counts[(metadata["qualifier"], metadata.get("quantity"))] += 1
        owner, group, alias_path, alias_line = choices[0]
        label = redact_secrets(".".join(_kotlin_name(name) for name in (owner, group, prop)))
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        alias_relative = alias_path.relative_to(root).as_posix() if alias_path.is_relative_to(root) else alias_path.name
        node_id = _make_id("cmp_accessor", relative, redact_secrets(obj), redact_secrets(prop), decl_salt(relative + "\0" + obj + "\0" + prop))
        if not any(qualifier_counts[(c["metadata"]["qualifier"], c["metadata"].get("quantity"))] == 1 for c in candidates):
            continue
        nodes.append({"id": node_id, "label": label, "file_type": "code", "source_file": str(path), "source_location": f"L{line}", "attributes": {"alias_source": alias_relative, "alias_location": f"L{alias_line}"}})
        for candidate in candidates:
            metadata = candidate["metadata"]
            if qualifier_counts[(metadata["qualifier"], metadata.get("quantity"))] != 1:
                continue
            edges.append({"source": node_id, "target": candidate["id"], "relation": "references", "confidence": "EXTRACTED", "source_file": str(path), "source_location": f"L{line}", "weight": 1.0})
