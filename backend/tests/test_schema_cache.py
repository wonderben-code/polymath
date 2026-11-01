"""Static JSON schemas are generated once per object and handed out as copies."""

from pydantic import BaseModel, TypeAdapter

from polymath import schema_cache
from polymath.research.models import ACTION_ADAPTER


class _Sample(BaseModel):
    name: str


def test_schema_is_generated_once_per_object_and_copied(monkeypatch):
    calls = []
    original = TypeAdapter.json_schema

    def counting(self, *args, **kwargs):
        calls.append(self)
        return original(self, *args, **kwargs)

    expected = ACTION_ADAPTER.json_schema()
    monkeypatch.setattr(TypeAdapter, "json_schema", counting)
    first = schema_cache.json_schema(ACTION_ADAPTER)
    second = schema_cache.json_schema(ACTION_ADAPTER)
    assert first == second == expected
    assert first is not second
    first["mutated"] = True
    assert "mutated" not in schema_cache.json_schema(ACTION_ADAPTER)
    assert calls.count(ACTION_ADAPTER) <= 1


def test_model_classes_and_swapped_adapters_get_their_own_entries():
    assert schema_cache.json_schema(_Sample) == _Sample.model_json_schema()
    other = TypeAdapter(_Sample)
    assert schema_cache.json_schema(other) == other.json_schema()
    assert schema_cache.json_schema(other) != schema_cache.json_schema(ACTION_ADAPTER)
