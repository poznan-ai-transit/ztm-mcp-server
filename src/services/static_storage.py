from __future__ import annotations
from contextlib import contextmanager
import threading
from typing import Any, Protocol
from immutabledict import immutabledict
from collections.abc import Mapping


class ReadOnlyStaticStorage(Protocol):
    def read_lock(self): ...

    def get_snapshot(self) -> immutabledict[str, Any]: ...

    def get_static_gtfs(self) -> immutabledict[str, Any]: ...

    def get_stops(self) -> tuple[immutabledict[str, str]]: ...

    def get_routes(self) -> tuple[immutabledict[str, str]]: ...

    def get_stop_times(self) -> tuple[immutabledict[str, str]]: ...

    def get_stop_by_id(self, stop_id: str) -> immutabledict[str, str] | None: ...

    def get_route_by_id(self, route_id: str) -> immutabledict[str, str] | None: ...

    def get_stop_times_for_trip(self, trip_id: str) -> tuple[immutabledict[str, str]]: ...

    def get_stop_times_for_stop(self, stop_id: str) -> tuple[immutabledict[str, str]]: ...


class WriteOnlyStaticStorage(Protocol):
    def set_static_gtfs(self, data: dict[str, Any]) -> None: ...


class StaticStorage:
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "StaticStorage":
        return cls()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._data: immutabledict[str, Any] = immutabledict()
        self._lock = threading.RLock()
        self._initialized = True

    def set_static_gtfs(self, data: dict[str, Any]) -> None:
        new_data = self._freeze(data)
        with self._lock:
            self._data = new_data

    def _freeze(self, value: Any) -> Any:
        if isinstance(value, immutabledict):
            return value
        if isinstance(value, dict):
            return immutabledict({key: self._freeze(item) for key, item in value.items()})
        if isinstance(value, (list, tuple)):
            return tuple(self._freeze(item) for item in value)
        return value
    
    @classmethod
    def to_plain(cls, value):
        if isinstance(value, Mapping):
            return {key: cls.to_plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls.to_plain(item) for item in value]
        return value

    @contextmanager
    def read_lock(self):
        with self._lock:
            yield

    def get_snapshot(self) -> immutabledict[str, Any]:
        with self._lock:
            return self._data

    def get_static_gtfs(self) -> immutabledict[str, Any]:
        with self._lock:
            return self._data

    def get_stops(self) -> tuple[immutabledict[str, str]]:
        with self._lock:
            return tuple(self._data.get("stops", []))

    def get_routes(self) -> tuple[immutabledict[str, str]]:
        with self._lock:
            return tuple(self._data.get("routes", []))

    def get_stop_times(self) -> tuple[immutabledict[str, str]]:
        with self._lock:
            return tuple(self._data.get("stop_times", []))

    def get_stop_by_id(self, stop_id: str) -> immutabledict[str, str] | None:
        with self._lock:
            return self._data.get("indexes", {}).get("stops_by_id", {}).get(stop_id)

    def get_route_by_id(self, route_id: str) -> immutabledict[str, str] | None:
        with self._lock:
            return self._data.get("indexes", {}).get("routes_by_id", {}).get(route_id)

    def get_stop_times_for_trip(self, trip_id: str) -> tuple[immutabledict[str, str]]:
        with self._lock:
            return tuple(
                self._data.get("indexes", {})
                .get("stop_times_by_trip_id", {})
                .get(trip_id, [])
            )

    def get_stop_times_for_stop(self, stop_id: str) -> tuple[immutabledict[str, str]]:
        with self._lock:
            return tuple(
                self._data.get("indexes", {})
                .get("stop_times_by_stop_id", {})
                .get(stop_id, [])
            )