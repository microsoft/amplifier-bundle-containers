"""Tests for MetadataStore: save, load, remove, list."""

from __future__ import annotations

from amplifier_module_tool_containers import MetadataStore  # re-exported from __init__


def test_save_and_load(metadata_store: MetadataStore):
    """Round-trip save then load returns same data."""
    data = {"name": "test-1", "image": "ubuntu:24.04", "purpose": "general"}
    metadata_store.save("test-1", data)
    loaded = metadata_store.load("test-1")
    assert loaded == data


def test_load_missing(metadata_store: MetadataStore):
    """Returns None for non-existent container."""
    assert metadata_store.load("nonexistent") is None


def test_remove(metadata_store: MetadataStore):
    """Removes metadata directory."""
    metadata_store.save("to-delete", {"name": "to-delete"})
    assert metadata_store.load("to-delete") is not None
    metadata_store.remove("to-delete")
    assert metadata_store.load("to-delete") is None


def test_remove_missing(metadata_store: MetadataStore):
    """Doesn't crash on non-existent."""
    metadata_store.remove("does-not-exist")  # Should not raise


def test_list_all_empty(metadata_store: MetadataStore):
    """Empty dir returns empty list."""
    assert metadata_store.list_all() == []


def test_list_all_with_entries(metadata_store: MetadataStore):
    """Returns all saved containers."""
    metadata_store.save("c1", {"name": "c1", "image": "alpine"})
    metadata_store.save("c2", {"name": "c2", "image": "ubuntu"})
    metadata_store.save("c3", {"name": "c3", "image": "python"})
    result = metadata_store.list_all()
    assert len(result) == 3
    names = {r["name"] for r in result}
    assert names == {"c1", "c2", "c3"}
