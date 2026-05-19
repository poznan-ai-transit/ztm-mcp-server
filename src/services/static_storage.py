from __future__ import annotations

from typing import Any


class StaticStorage:
    def __init__(self, initial_data: dict[str, Any] | None = None):
        self._data: dict[str, Any] = {}
        if initial_data:
            self.set_static_gtfs(initial_data)

    def set_static_gtfs(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def get_static_gtfs(self) -> dict[str, Any]:
        return dict(self._data)

    def get_stops(self) -> list[dict[str, str]]:
        return list(self._data.get("stops", []))

    def get_routes(self) -> list[dict[str, str]]:
        return list(self._data.get("routes", []))

    def get_stop_times(self) -> list[dict[str, str]]:
        return list(self._data.get("stop_times", []))

    def get_stop_by_id(self, stop_id: str) -> dict[str, str] | None:
        return self._data.get("indexes", {}).get("stops_by_id", {}).get(stop_id)

    def get_route_by_id(self, route_id: str) -> dict[str, str] | None:
        return self._data.get("indexes", {}).get("routes_by_id", {}).get(route_id)

    def get_stop_times_for_trip(self, trip_id: str) -> list[dict[str, str]]:
        return list(
            self._data.get("indexes", {}).get("stop_times_by_trip_id", {}).get(trip_id, [])
        )

    def get_stop_times_for_stop(self, stop_id: str) -> list[dict[str, str]]:
        return list(
            self._data.get("indexes", {}).get("stop_times_by_stop_id", {}).get(stop_id, [])
        )

    def clear(self) -> None:
        self._data.clear()