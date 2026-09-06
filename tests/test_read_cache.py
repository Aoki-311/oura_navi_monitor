from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.repositories.read_cache import PublishedReadCache
from app.repositories.analytics_repository import AnalyticsRepository
from app.settings import Settings


def test_parallel_readers_share_one_query_and_receive_independent_values():
    cache = PublishedReadCache()
    entered, release = Event(), Event()
    calls = []

    def load():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return [{"labels": ["original"]}]

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(cache.read, ("run-1", "roster"), load)
        assert entered.wait(2)
        others = [pool.submit(cache.read, ("run-1", "roster"), load) for _ in range(2)]
        release.set()
        results = [first.result(), *(item.result() for item in others)]
    assert len(calls) == 1
    results[0][0]["labels"].append("response-only")
    assert results[1] == [{"labels": ["original"]}]
    assert cache.read(("run-1", "roster"), load) == results[1]


def test_mutable_pointer_is_read_again_and_failure_is_not_retained():
    cache = PublishedReadCache()
    calls = []

    def load():
        calls.append(1)
        return {"run": len(calls)}

    assert cache.read("pointer", load, retain=False) == {"run": 1}
    assert cache.read("pointer", load, retain=False) == {"run": 2}
    with pytest.raises(ValueError, match="provider failed"):
        cache.read("retry", lambda: (_ for _ in ()).throw(ValueError("provider failed")))
    assert cache.read("retry", lambda: {"run": 3}) == {"run": 3}


def test_result_size_and_entry_limits_evict_without_changing_values():
    cache = PublishedReadCache(max_entries=1, max_bytes=20)
    calls = []

    def load(value):
        calls.append(value)
        return value

    for key in ("a", "b", "a"):
        assert cache.read(key, lambda: load(key)) == key
    assert calls == ["a", "b", "a"]
    for _ in range(2):
        assert cache.read("large", lambda: load("x" * 100)) == "x" * 100
    assert len(calls) == 5


def test_repository_retains_only_immutable_roster_and_separates_publications():
    class Job:
        def __init__(self, rows):
            self.rows = rows

        def result(self):
            return self.rows

    class Client:
        def __init__(self):
            self.calls = []

        def query(self, sql, *, job_config, location):
            run = next(p.value for p in job_config.query_parameters if p.name == "published_run_id")
            self.calls.append(run)
            return Job([{"snapshot_run_id": run, "label_ids_json": "[]"}])

    client = Client()
    repository = AnalyticsRepository(Settings(), client=client)
    first = repository.published_roster_snapshot(published_run_id="run-1")
    first[0]["label_ids_json"] = "modified"
    assert repository.published_roster_snapshot(published_run_id="run-1")[0]["label_ids_json"] == "[]"
    assert repository.published_roster_snapshot(published_run_id="run-2")[0]["snapshot_run_id"] == "run-2"
    assert client.calls == ["run-1", "run-2"]
