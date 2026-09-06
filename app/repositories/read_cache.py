from __future__ import annotations

import json
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from typing import Any, Callable, Hashable


class PublishedReadCache:
    """Coalesce concurrent reads; retain only explicitly version-bound results.

    Mutable publication pointers use retain=False: the next refresh always asks
    the source again. Facts may be retained only with their exact publication,
    SQL and parameters in the key. Exceptions are never cached. Copies prevent
    one response's projections from modifying another response's data.
    """

    def __init__(self, *, max_entries: int = 32, max_bytes: int = 16 * 1024 * 1024):
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._values: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._pending: dict[Hashable, Future] = {}
        self._lock = Lock()

    def read(self, key: Hashable, loader: Callable[[], Any], *, retain: bool = True) -> Any:
        with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                self._values.move_to_end(key)
                return deepcopy(cached[0])
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._pending[key] = future
        if not owner:
            return deepcopy(future.result())
        try:
            value = loader()
            size = len(json.dumps(value, default=str, ensure_ascii=False).encode()) if retain else 0
            with self._lock:
                if retain and size <= self._max_bytes:
                    self._values[key] = (deepcopy(value), size)
                    self._bytes += size
                    while len(self._values) > self._max_entries or self._bytes > self._max_bytes:
                        _, (_, removed_size) = self._values.popitem(last=False)
                        self._bytes -= removed_size
            future.set_result(value)
            return deepcopy(value)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
