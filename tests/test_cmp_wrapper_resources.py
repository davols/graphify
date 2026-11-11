"""CMP wrapper accessors should be linked to their actual localized resources."""

from graphify.build import build_from_json
from graphify.extract import collect_files, extract
from graphify.serve import _query_graph_text


def test_search_returns_real_accessor_for_localized_cmp_resource(tmp_path):
    root = tmp_path / "app" / "src" / "commonMain"
    kotlin = root / "kotlin" / "com" / "example"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text(
        'package com.example\nobject AppStrings { val `boligLoanDetails` = BoligLoanDetails }\n',
        encoding="utf-8",
    )
    (kotlin / "BoligLoanDetailsStrings.kt").write_text(
        'package com.example\nobject BoligLoanDetails { val `iban` = Res.string.boligloandetails_iban }\n',
        encoding="utf-8",
    )
    for locale, text in (("values", "IBAN"), ("values-en", "International account")):
        directory = root / "composeResources" / locale
        directory.mkdir(parents=True)
        (directory / "boligloandetails_strings.xml").write_text(
            f'<resources><string name="boligloandetails_iban">{text}</string></resources>',
            encoding="utf-8",
        )
    result = extract(collect_files(root, root=root), cache_root=tmp_path, root=root)
    graph = build_from_json(result, root=root)
    norwegian = _query_graph_text(graph, "IBAN", depth=1)
    english = _query_graph_text(graph, "International account", depth=1)
    assert "AppStrings.boligLoanDetails.iban" in norwegian
    assert "AppStrings.boligLoanDetails.iban" in english
    accessor = next(data for _, data in graph.nodes(data=True) if data.get("label") == "AppStrings.boligLoanDetails.iban")
    assert accessor["attributes"]["alias_source"] == "kotlin/com/example/AppStrings.kt"
    assert accessor["attributes"]["alias_location"] == "L2"


def test_incremental_rebuild_updates_alias_without_leaving_stale_accessor(tmp_path):
    import json

    from graphify.watch import _rebuild_code

    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    alias = kotlin / "AppStrings.kt"
    alias.write_text('object AppStrings {\n val `receivers` = Receivers\n}\n', encoding="utf-8")
    (kotlin / "Receivers.kt").write_text(
        'object Receivers {\n val `title` = Res.string.receivers_title\n}\n', encoding="utf-8"
    )
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    (directory / "receivers_strings.xml").write_text(
        '<resources><string name="receivers_title">Mine mottakere</string></resources>', encoding="utf-8"
    )
    assert _rebuild_code(root, acquire_lock=False, no_cluster=True)
    alias.write_text('object AppStrings {\n val `recipients` = Receivers\n}\n', encoding="utf-8")
    assert _rebuild_code(root, changed_paths=[alias], acquire_lock=False, no_cluster=True)
    graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    labels = {node["label"] for node in graph["nodes"]}
    assert "AppStrings.recipients.title" in labels
    assert "AppStrings.receivers.title" not in labels


def test_plural_wrappers_are_linked_to_each_quantity(tmp_path):
    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text('object AppStrings {\n val `bank` = Bank\n}\n', encoding="utf-8")
    (kotlin / "Bank.kt").write_text(
        'object Bank {\n val `accounts` = Res.plurals.bank_accounts\n}\n', encoding="utf-8"
    )
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    (directory / "bank_strings.xml").write_text(
        '<resources><plurals name="bank_accounts"><item quantity="one">%1$d konto</item>'
        '<item quantity="other">%1$d kontoer</item></plurals></resources>', encoding="utf-8"
    )
    graph = build_from_json(extract(collect_files(root, root=root), cache_root=tmp_path, root=root), root=root)
    assert "AppStrings.bank.accounts" in _query_graph_text(graph, "kontoer", depth=1)


def test_incremental_rebuild_removes_accessor_when_resource_is_deleted(tmp_path):
    import json

    from graphify.watch import _rebuild_code

    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text('object AppStrings {\n val `receivers` = Receivers\n}\n', encoding="utf-8")
    (kotlin / "Receivers.kt").write_text(
        'object Receivers {\n val `title` = Res.string.receivers_title\n}\n', encoding="utf-8"
    )
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    xml = directory / "receivers_strings.xml"
    xml.write_text('<resources><string name="receivers_title">Mine mottakere</string></resources>', encoding="utf-8")
    assert _rebuild_code(root, acquire_lock=False, no_cluster=True)
    xml.unlink()
    assert _rebuild_code(root, changed_paths=[xml], acquire_lock=False, no_cluster=True)
    graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    assert "AppStrings.receivers.title" not in {node["label"] for node in graph["nodes"]}


def test_ambiguous_resource_names_do_not_bind_to_a_wrapper(tmp_path):
    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text('object AppStrings {\n val `bank` = Bank\n}\n', encoding="utf-8")
    (kotlin / "Bank.kt").write_text('object Bank {\n val `title` = Res.string.bank_title\n}\n', encoding="utf-8")
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    for file in ("first.xml", "second.xml"):
        (directory / file).write_text('<resources><string name="bank_title">Bank</string></resources>', encoding="utf-8")
    graph = build_from_json(extract(collect_files(root, root=root), cache_root=tmp_path, root=root), root=root)
    assert "AppStrings.bank.title" not in {data["label"] for _, data in graph.nodes(data=True)}


def test_cli_incremental_rebuild_updates_accessor(tmp_path):
    import json
    import subprocess
    import sys

    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    alias = kotlin / "AppStrings.kt"
    alias.write_text('object AppStrings {\n val `receivers` = Receivers\n}\n', encoding="utf-8")
    (kotlin / "Receivers.kt").write_text(
        'object Receivers {\n val `title` = Res.string.receivers_title\n}\n', encoding="utf-8"
    )
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    xml = directory / "receivers_strings.xml"
    xml.write_text(
        '<resources><string name="receivers_title">Mine mottakere</string></resources>', encoding="utf-8"
    )
    command = [sys.executable, "-m", "graphify", "extract", str(root), "--code-only", "--no-cluster"]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    alias.write_text('object AppStrings {\n val `recipients` = Receivers\n}\n', encoding="utf-8")
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    labels = {node["label"] for node in graph["nodes"]}
    assert "AppStrings.recipients.title" in labels
    assert "AppStrings.receivers.title" not in labels
    xml.write_text('<resources><string name="receivers_title">New label</string></resources>', encoding="utf-8")
    third = subprocess.run(command, capture_output=True, text=True)
    assert third.returncode == 0, third.stderr
    graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    labels = {node["label"] for node in graph["nodes"]}
    assert "receivers_title: New label" in labels
    assert "receivers_title: Mine mottakere" not in labels
    assert "AppStrings.recipients.title" in labels
    xml.unlink()
    fourth = subprocess.run(command, capture_output=True, text=True)
    assert fourth.returncode == 0, fourth.stderr
    graph = json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    assert "AppStrings.recipients.title" not in {node["label"] for node in graph["nodes"]}


def test_separate_compose_source_sets_do_not_cross_link(tmp_path):
    for module, alias, value in (("first", "firstBank", "First"), ("second", "secondBank", "Second")):
        root = tmp_path / module / "commonMain"
        kotlin = root / "kotlin"
        kotlin.mkdir(parents=True)
        (kotlin / "AppStrings.kt").write_text(f'object AppStrings {{\n val `{alias}` = Bank\n}}\n', encoding="utf-8")
        (kotlin / "Bank.kt").write_text('object Bank {\n val `title` = Res.string.bank_title\n}\n', encoding="utf-8")
        directory = root / "composeResources" / "values"
        directory.mkdir(parents=True)
        (directory / "bank_strings.xml").write_text(
            f'<resources><string name="bank_title">{value}</string></resources>', encoding="utf-8"
        )
    graph = build_from_json(extract(collect_files(tmp_path, root=tmp_path), cache_root=tmp_path, root=tmp_path), root=tmp_path)
    resources = {data["label"]: node for node, data in graph.nodes(data=True) if data.get("label", "").startswith("bank_title:")}
    first = {graph.nodes[node]["label"] for node in graph.neighbors(resources["bank_title: First"])}
    second = {graph.nodes[node]["label"] for node in graph.neighbors(resources["bank_title: Second"])}
    assert "AppStrings.firstBank.title" in first
    assert "AppStrings.secondBank.title" not in first
    assert "AppStrings.secondBank.title" in second
    assert "AppStrings.firstBank.title" not in second


def test_kotlin_keyword_accessor_is_rendered_with_backticks(tmp_path):
    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text('object AppStrings {\n val `bank` = Bank\n}\n', encoding="utf-8")
    (kotlin / "Bank.kt").write_text('object Bank {\n val `when` = Res.string.bank_when\n}\n', encoding="utf-8")
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    (directory / "bank_strings.xml").write_text('<resources><string name="bank_when">When</string></resources>', encoding="utf-8")
    graph = build_from_json(extract(collect_files(root, root=root), cache_root=tmp_path, root=root), root=root)
    assert "AppStrings.bank.`when`" in _query_graph_text(graph, "When", depth=1)


def test_non_public_wrapper_properties_are_not_suggested(tmp_path):
    root = tmp_path / "commonMain"
    kotlin = root / "kotlin"
    kotlin.mkdir(parents=True)
    (kotlin / "AppStrings.kt").write_text('object AppStrings {\n val `bank` = Bank\n}\n', encoding="utf-8")
    (kotlin / "Bank.kt").write_text('object Bank {\n private val `title` = Res.string.bank_title\n}\n', encoding="utf-8")
    directory = root / "composeResources" / "values"
    directory.mkdir(parents=True)
    (directory / "bank_strings.xml").write_text('<resources><string name="bank_title">Bank</string></resources>', encoding="utf-8")
    graph = build_from_json(extract(collect_files(root, root=root), cache_root=tmp_path, root=root), root=root)
    assert "AppStrings.bank.title" not in {data["label"] for _, data in graph.nodes(data=True)}
