import json

from socrata_mcp.cache import DiskCache


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_cache(tmp_path, clock=None):
    return DiskCache(tmp_path / "cache", clock=clock or FakeClock())


def test_roundtrip(tmp_path):
    cache = make_cache(tmp_path)
    cache.set("query", {"url": "http://x", "params": {"a": 1}}, {"rows": [1, 2]})
    assert cache.get("query", {"url": "http://x", "params": {"a": 1}}, ttl=60) == {
        "rows": [1, 2]
    }


def test_miss_returns_none(tmp_path):
    cache = make_cache(tmp_path)
    assert cache.get("query", {"url": "http://y"}, ttl=60) is None


def test_expiry(tmp_path):
    clock = FakeClock(now=1000.0)
    cache = make_cache(tmp_path, clock=clock)
    cache.set("metadata", "some-key", {"v": 1})
    clock.now = 1059.0
    assert cache.get("metadata", "some-key", ttl=60) == {"v": 1}
    clock.now = 1061.0
    assert cache.get("metadata", "some-key", ttl=60) is None


def test_key_stable_across_dict_order(tmp_path):
    cache = make_cache(tmp_path)
    cache.set("query", {"a": 1, "b": 2}, "data")
    assert cache.get("query", {"b": 2, "a": 1}, ttl=60) == "data"


def test_kinds_do_not_collide(tmp_path):
    cache = make_cache(tmp_path)
    cache.set("query", "k", "query-data")
    cache.set("metadata", "k", "metadata-data")
    assert cache.get("query", "k", ttl=60) == "query-data"
    assert cache.get("metadata", "k", ttl=60) == "metadata-data"


def test_corrupt_file_is_a_miss(tmp_path):
    cache = make_cache(tmp_path)
    cache.set("query", "k", {"v": 1})
    (path,) = list((tmp_path / "cache" / "query").iterdir())
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("query", "k", ttl=60) is None


def test_zero_ttl_bypasses_cache(tmp_path):
    cache = make_cache(tmp_path)
    cache.set("query", "k", {"v": 1})
    assert cache.get("query", "k", ttl=0) is None


def test_get_or_fetch_fetches_once(tmp_path):
    cache = make_cache(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return {"fresh": True}

    data, cached = cache.get_or_fetch("catalog", "k", ttl=60, fetch=fetch)
    assert data == {"fresh": True} and cached is False
    data, cached = cache.get_or_fetch("catalog", "k", ttl=60, fetch=fetch)
    assert data == {"fresh": True} and cached is True
    assert len(calls) == 1


def test_stored_file_is_json_with_cached_at(tmp_path):
    clock = FakeClock(now=1234.0)
    cache = make_cache(tmp_path, clock=clock)
    cache.set("metadata", "k", {"v": 1})
    (path,) = list((tmp_path / "cache" / "metadata").iterdir())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cached_at"] == 1234.0
    assert payload["data"] == {"v": 1}
