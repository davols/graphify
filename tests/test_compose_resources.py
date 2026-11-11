"""Compose resource strings should be searchable without an AI model."""

from __future__ import annotations

import pytest

from graphify.build import build_from_json
from graphify.detect import FileType, classify_file
from graphify.extract import collect_files, extract
from graphify.serve import _query_graph_text


@pytest.mark.parametrize("resource_directory", ["composeResources", "res"])
def test_localized_strings_are_searchable_with_key_and_source(tmp_path, resource_directory):
    resources = tmp_path / "app" / "src" / "commonMain" / resource_directory
    norwegian = resources / "values" / "bank_strings.xml"
    english = resources / "values-en" / "bank_strings.xml"
    norwegian.parent.mkdir(parents=True)
    english.parent.mkdir(parents=True)
    norwegian.write_text(
        '<resources>\n  <string name="bank_credit_card">Kredittkort</string>\n</resources>\n',
        encoding="utf-8",
    )
    english.write_text(
        '<resources>\n  <string name="bank_credit_card">Credit card</string>\n</resources>\n',
        encoding="utf-8",
    )

    assert classify_file(norwegian) is FileType.CODE
    assert classify_file(english) is FileType.CODE
    paths = collect_files(resources, root=tmp_path)
    assert set(paths) == {norwegian, english}
    graph = build_from_json(extract(paths, cache_root=tmp_path, root=tmp_path), root=tmp_path)

    norwegian_answer = _query_graph_text(graph, "Kredittkort", depth=2)
    assert "bank_credit_card" in norwegian_answer
    assert "values/bank_strings.xml" in norwegian_answer
    assert "L2" in norwegian_answer

    english_answer = _query_graph_text(graph, "Credit card", depth=2)
    assert "bank_credit_card" in english_answer
    assert "values-en/bank_strings.xml" in english_answer


def test_non_resource_xml_is_not_claimed(tmp_path):
    other = tmp_path / "docs" / "bank_strings.xml"
    other.parent.mkdir()
    other.write_text('<resources><string name="x">value</string></resources>', encoding="utf-8")
    assert classify_file(other) is None
    assert collect_files(tmp_path) == []


def test_plural_forms_are_searchable_with_quantity_and_placeholders(tmp_path):
    resources = tmp_path / "composeResources" / "values"
    resources.mkdir(parents=True)
    path = resources / "bank_strings.xml"
    path.write_text(
        '<resources>\n'
        '  <plurals name="bank_accounts">\n'
        '    <item quantity="one">%1$d konto</item>\n'
        '    <item quantity="other">%1$d kontoer</item>\n'
        '  </plurals>\n'
        '</resources>\n',
        encoding="utf-8",
    )
    graph = build_from_json(extract([path], cache_root=tmp_path, root=tmp_path), root=tmp_path)
    answer = _query_graph_text(graph, "kontoer", depth=2)
    assert "bank_accounts" in answer
    assert "other" in answer
    assert "%1$d kontoer" in answer
    assert "L4" in answer


def test_resource_changes_are_treated_as_code_by_watch(tmp_path):
    from graphify.watch import _batch_triggers_rebuild, _has_non_code

    path = tmp_path / "composeResources" / "values" / "bank_strings.xml"
    path.parent.mkdir(parents=True)
    path.write_text('<resources><string name="a">A</string></resources>', encoding="utf-8")
    assert _batch_triggers_rebuild([path])
    assert not _has_non_code([path])


def test_distinct_resource_names_with_similar_ids_do_not_collapse(tmp_path):
    resources = tmp_path / "composeResources" / "values"
    resources.mkdir(parents=True)
    path = resources / "strings.xml"
    path.write_text(
        '<resources><string name="a_b">First</string>'
        '<string name="a__b">Second</string></resources>',
        encoding="utf-8",
    )
    graph = build_from_json(extract([path], cache_root=tmp_path, root=tmp_path), root=tmp_path)
    labels = {node.get("label") for _, node in graph.nodes(data=True)}
    assert "a_b: First" in labels
    assert "a__b: Second" in labels


def test_resource_text_preserves_markup_and_rejects_entity_declarations(tmp_path):
    from graphify.extractors.android_resources import extract_android_resources

    resources = tmp_path / "composeResources" / "values"
    resources.mkdir(parents=True)
    path = resources / "strings.xml"
    path.write_text(
        '<resources><string name="hello">Hei <b>%1$s</b> &amp; velkommen</string></resources>',
        encoding="utf-8",
    )
    result = extract_android_resources(path)
    assert any(node["label"] == "hello: Hei %1$s & velkommen" for node in result["nodes"])

    path.write_text(
        '<!DOCTYPE resources [<!ENTITY x "unsafe">]><resources>'
        '<string name="hello">&x;</string></resources>',
        encoding="utf-8",
    )
    rejected = extract_android_resources(path)
    assert rejected["nodes"] == []
    assert "DOCTYPE" in rejected["error"]

    path.write_bytes(
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE resources [<!ENTITY x "unsafe">]><resources>'
        '<string name="hello">&x;</string></resources>'.encode("utf-16")
    )
    assert extract_android_resources(path)["nodes"] == []


def test_explicit_xml_language_override_is_respected(tmp_path):
    from graphify.rcfile import activate_language_overrides, get_language_overrides, set_language_overrides

    resources = tmp_path / "composeResources" / "values"
    resources.mkdir(parents=True)
    path = resources / "strings.xml"
    path.write_text('<resources><string name="hello">Hei</string></resources>', encoding="utf-8")
    (tmp_path / ".graphifyrc").write_text("language.xml=.txt\n", encoding="utf-8")
    previous = get_language_overrides()
    try:
        activate_language_overrides(tmp_path)
        assert classify_file(path) is FileType.DOCUMENT
        assert path not in collect_files(tmp_path)
        (tmp_path / ".graphifyrc").write_text("language.xml=.yaml\n", encoding="utf-8")
        activate_language_overrides(tmp_path)
        assert classify_file(path) is FileType.CODE
        assert path in collect_files(tmp_path)
    finally:
        set_language_overrides(previous)


def test_incremental_rebuild_replaces_changed_resource_value(tmp_path):
    import json

    from graphify.watch import _rebuild_code

    corpus = tmp_path / "corpus"
    path = corpus / "composeResources" / "values" / "strings.xml"
    path.parent.mkdir(parents=True)
    path.write_text('<resources><string name="title">Old</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, acquire_lock=False, no_cluster=True)

    path.write_text('<resources><string name="title">New</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, changed_paths=[path], acquire_lock=False, no_cluster=True)
    graph = json.loads((corpus / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    labels = {node["label"] for node in graph["nodes"]}
    assert "title: New" in labels
    assert "title: Old" not in labels


def test_secret_shaped_resource_text_is_redacted_before_caching(tmp_path):
    import json

    path = tmp_path / "composeResources" / "values" / "strings.xml"
    path.parent.mkdir(parents=True)
    secret = "AIza" + "A" * 35
    path.write_text(f'<resources><string name="maps_key">{secret}</string></resources>', encoding="utf-8")
    result = extract([path], cache_root=tmp_path, root=tmp_path)
    assert secret not in json.dumps(result)
    assert any("[redacted]" in node.get("label", "") for node in result["nodes"])
    for cached in (tmp_path / "graphify-out" / "cache").rglob("*.json"):
        assert secret not in cached.read_text(encoding="utf-8")


def test_incremental_rebuild_preserves_resources_in_similarly_named_paths(tmp_path):
    import json

    from graphify.watch import _rebuild_code

    corpus = tmp_path / "corpus"
    first = corpus / "foo-bar" / "res" / "values" / "strings.xml"
    second = corpus / "foo_bar" / "res" / "values" / "strings.xml"
    first.parent.mkdir(parents=True)
    first.write_text('<resources><string name="title">First</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, acquire_lock=False, no_cluster=True)

    second.parent.mkdir(parents=True)
    second.write_text('<resources><string name="title">Second</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, changed_paths=[second], acquire_lock=False, no_cluster=True)
    graph_path = corpus / "graphify-out" / "graph.json"
    labels = {node["label"] for node in json.loads(graph_path.read_text(encoding="utf-8"))["nodes"]}
    assert {"title: First", "title: Second"} <= labels

    first.write_text('<resources><string name="title">Updated</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, changed_paths=[first], acquire_lock=False, no_cluster=True)
    labels = {node["label"] for node in json.loads(graph_path.read_text(encoding="utf-8"))["nodes"]}
    assert {"title: Updated", "title: Second"} <= labels
    assert "title: First" not in labels


def test_incremental_rebuild_keeps_symlinked_resource_paths_distinct(tmp_path):
    import json

    from graphify.watch import _rebuild_code

    corpus = tmp_path / "corpus"
    first = corpus / "foo-bar" / "res" / "values" / "strings.xml"
    second = corpus / "foo_bar" / "res" / "values" / "strings.xml"
    first.parent.mkdir(parents=True)
    first.write_text('<resources><string name="title">Title</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, acquire_lock=False, no_cluster=True)
    second.parent.mkdir(parents=True)
    try:
        second.symlink_to(first)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    assert _rebuild_code(corpus, changed_paths=[second], acquire_lock=False, no_cluster=True)
    graph = json.loads((corpus / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    sources = {node.get("source_file") for node in graph["nodes"] if node.get("label") == "title: Title"}
    assert "foo-bar/res/values/strings.xml" in sources
    assert "foo_bar/res/values/strings.xml" in sources

    first.write_text('<resources><string name="title">Updated</string></resources>', encoding="utf-8")
    assert _rebuild_code(corpus, changed_paths=[first], acquire_lock=False, no_cluster=True)
    graph = json.loads((corpus / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    updated = {node.get("source_file") for node in graph["nodes"] if node.get("label") == "title: Updated"}
    assert updated == sources
    assert not any(node.get("label") == "title: Title" for node in graph["nodes"])


def test_resource_ids_are_portable_across_checkout_locations(tmp_path):
    results = []
    for checkout in (tmp_path / "first", tmp_path / "second"):
        path = checkout / "composeResources" / "values" / "strings.xml"
        path.parent.mkdir(parents=True)
        path.write_text('<resources><string name="title">Title</string></resources>', encoding="utf-8")
        result = extract([path], cache_root=checkout, root=checkout)
        results.append({node["id"] for node in result["nodes"]})
    assert results[0] == results[1]


def test_android_item_strings_and_escaped_values_are_searchable(tmp_path):
    path = tmp_path / "app" / "src" / "main" / "res" / "values" / "strings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<resources>\n'
        '  <string name="welcome">Don\\\'t \\u0066orget: %1$s</string>\n'
        '  <string name="emoji">\\uD83D\\uDE80</string>\n'
        '  <string name="literal">\\\\u0041</string>\n'
        '  <item type="string" name="status">Ready</item>\n'
        '  <plurals name="things"><item quantity="other">Don\\\'t %1$d</item></plurals>\n'
        '</resources>\n',
        encoding="utf-8",
    )
    graph = build_from_json(extract([path], cache_root=tmp_path, root=tmp_path), root=tmp_path)
    labels = {node.get("label") for _, node in graph.nodes(data=True)}
    assert "welcome: Don't forget: %1$s" in labels
    assert "emoji: 🚀" in labels
    assert r"literal: \u0041" in labels
    assert "status: Ready" in labels
    assert "things (other): Don't %1$d" in labels


def test_android_quotes_whitespace_and_escaped_newlines_are_readable(tmp_path):
    path = tmp_path / "app" / "src" / "main" / "res" / "values" / "strings.xml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<resources>\n'
        '  <string name="quoted">"  Keep   spaces  "</string>\n'
        '  <string name="collapsed">Many   spaces</string>\n'
        '  <string name="trimmed">  Before  </string>\n'
        '  <string name="wide">A\u2003\u2003B</string>\n'
        '  <string name="nbsp">A\u00a0\u00a0B</string>\n'
        '  <string name="multiline">Hello\\nWorld\\tNow</string>\n'
        '  <string name="plain">Hello World Now</string>\n'
        '</resources>\n',
        encoding="utf-8",
    )
    graph = build_from_json(extract([path], cache_root=tmp_path, root=tmp_path), root=tmp_path)
    labels = {node.get("label") for _, node in graph.nodes(data=True)}
    assert "quoted:   Keep   spaces  " in labels
    assert "collapsed: Many spaces" in labels
    assert "trimmed: Before" in labels
    assert "wide: A\u2003\u2003B" in labels
    assert "nbsp: A\u00a0\u00a0B" in labels
    assert "multiline: Hello World Now" in labels
    answer = _query_graph_text(graph, "Hello World Now", depth=1)
    assert "multiline" in answer
    assert r"Hello\nWorld\tNow" in answer
    assert "multiline" in _query_graph_text(graph, "Hello\nWorld\tNow", depth=1)


@pytest.mark.parametrize("contents, reason", [
    (b"<resources><string", "malformed"),
    (b"x" * (2 * 1024 * 1024 + 1), "too large"),
])
def test_invalid_resource_files_report_the_reason(tmp_path, capsys, contents, reason):
    path = tmp_path / "composeResources" / "values" / "strings.xml"
    path.parent.mkdir(parents=True)
    path.write_bytes(contents)
    result = extract([path], cache_root=tmp_path, root=tmp_path, parallel=False)
    assert str(path) in result["failed_sources"]
    diagnostic = capsys.readouterr().err
    assert "strings.xml" in diagnostic
    assert reason in diagnostic


def test_watch_rebuilds_after_atomic_resource_replacement(tmp_path, monkeypatch):
    pytest.importorskip("watchdog")
    import sys

    from watchdog import observers
    from watchdog.events import FileMovedEvent
    from watchdog.observers import polling

    from graphify import watch as watch_mod

    root = tmp_path / "corpus"
    target = root / "composeResources" / "values" / "strings.xml"
    target.parent.mkdir(parents=True)
    target.write_text('<resources><string name="a">A</string></resources>', encoding="utf-8")
    temporary = target.parent / ".strings.xml.tmp"
    calls = []

    class FakeObserver:
        def schedule(self, handler, path, *, recursive=False, event_filter=None):
            self.handler = handler

        def start(self):
            self.handler.on_any_event(FileMovedEvent(str(temporary), str(target)))

        def stop(self):
            pass

        def join(self):
            pass

    sleeps = iter((None, KeyboardInterrupt()))

    def sleep_once(_seconds):
        result = next(sleeps)
        if isinstance(result, BaseException):
            raise result

    monkeypatch.setattr(observers, "Observer", FakeObserver)
    monkeypatch.setattr(polling, "PollingObserver", FakeObserver)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(watch_mod.time, "sleep", sleep_once)
    monkeypatch.setattr(watch_mod, "_rebuild_code", lambda path: calls.append(path) or True)
    watch_mod.watch(root, debounce=0)
    assert calls == [root]
