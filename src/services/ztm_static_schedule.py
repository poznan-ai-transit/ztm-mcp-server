from __future__ import annotations

import threading
from _thread import LockType
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


class ZTMStaticSchedule:
    _instance: ZTMStaticSchedule | None = None
    _instance_lock: LockType = threading.Lock()

    @classmethod
    def instance(cls) -> ZTMStaticSchedule:
        return cls()

    def __new__(cls) -> ZTMStaticSchedule:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._data = {}
                    cls._instance._lock = threading.RLock()
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._initialized = True

    def set_static_gtfs(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._data: dict[str, Any] = data

    @contextmanager
    def read_lock(self) -> Generator[None, None, None]:
        with self._lock:
            yield

    def get_static_gtfs(self) -> dict[str, Any]:
        with self._lock:
            return self._data

    def get_stops(self) -> list[dict[str, str]]:
        with self._lock:
            return self._data.get("stops", [])

    def get_routes(self) -> list[dict[str, str]]:
        with self._lock:
            return self._data.get("routes", [])

    def get_stop_times(self) -> list[dict[str, str]]:
        with self._lock:
            return self._data.get("stop_times", [])

    def _indexes(self) -> dict[str, Any]:
        return self._data.get("indexes", {})

    def get_stop_by_id(self, stop_id: str) -> dict[str, str] | None:
        with self._lock:
            return self._indexes().get("stops_by_id", {}).get(stop_id)

    def get_route_by_id(self, route_id: str) -> dict[str, str] | None:
        with self._lock:
            return self._indexes().get("routes_by_id", {}).get(route_id)

    def get_stop_times_for_trip(self, trip_id: str) -> list[dict[str, str]]:
        with self._lock:
            return self._indexes().get("stop_times_by_trip_id", {}).get(trip_id, [])

    def get_stop_times_for_stop(self, stop_id: str) -> list[dict[str, str]]:
        with self._lock:
            return self._indexes().get("stop_times_by_stop_id", {}).get(stop_id, [])
