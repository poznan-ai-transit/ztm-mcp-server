from __future__ import annotations

import csv
import io
import threading
import time
import zipfile
from datetime import datetime, timedelta
from typing import Any

from services.static_storage import StaticStorage


class ZTMService:
    def __init__(self, api_client: Any | None = None):
        self._api_client = api_client

    def start_daily_refresh(self, storage: StaticStorage) -> None:
        def _runner() -> None:
            storage.set_static_gtfs(self.get_static_gtfs())
            while True:
                sleep_for = self._seconds_until_next_six_am(datetime.now())
                time.sleep(sleep_for)
                storage.set_static_gtfs(self.get_static_gtfs())

        thread = threading.Thread(target=_runner, name="ztm-static-refresh", daemon=True)
        thread.start()

    def get_static_gtfs(self) -> dict[str, Any]:
        # mock data in the same shape as the real API (zip with GTFS files)
        zf = self._mock_gtfs_zip()
        stops = self._read_csv_from_zip(zf, "stops.txt")
        routes = self._read_csv_from_zip(zf, "routes.txt")
        stop_times = self._read_csv_from_zip(zf, "stop_times.txt")

        return {
            "stops": stops,
            "routes": routes,
            "stop_times": stop_times,
            "indexes": self._build_indexes(stops, routes, stop_times),
        }

    def _read_csv_from_zip(self, zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
        with zf.open(filename) as f:
            text = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader]

    def _build_indexes(
        self,
        stops: list[dict[str, str]],
        routes: list[dict[str, str]],
        stop_times: list[dict[str, str]],
    ) -> dict[str, Any]:
        stops_by_id = {row["stop_id"]: row for row in stops}
        routes_by_id = {row["route_id"]: row for row in routes}
        stop_times_by_trip_id: dict[str, list[dict[str, str]]] = {}
        stop_times_by_stop_id: dict[str, list[dict[str, str]]] = {}

        for row in stop_times:
            stop_times_by_trip_id.setdefault(row["trip_id"], []).append(row)
            stop_times_by_stop_id.setdefault(row["stop_id"], []).append(row)

        return {
            "stops_by_id": stops_by_id,
            "routes_by_id": routes_by_id,
            "stop_times_by_trip_id": stop_times_by_trip_id,
            "stop_times_by_stop_id": stop_times_by_stop_id,
        }

    def _seconds_until_next_six_am(self, now: datetime) -> float:
        next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        return (next_run - now).total_seconds()

    def _mock_gtfs_payload(self) -> tuple[str, str, str]:
        stops_csv = (
            "stop_id,stop_name,stop_lat,stop_lon\n"
            "4317,POZNAN GLOWNY,52.4019,16.9119\n"
            "3002,WLADYSLAWA,52.3962,16.9081\n"
            "1807,ZABINKO,52.3100,16.8740\n"
        )
        routes_csv = (
            "route_id,agency_id,route_short_name,route_long_name,route_type\n"
            "PKS,16,PKS,ZABINKO - POZNAN GLOWNY,3\n"
            "1,2,1,JUNIKOWO - FRANOWO,0\n"
        )
        stop_times_csv = (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign,"
            "pickup_type,drop_off_type\n"
            "1_765177_Y,05:38:00,05:38:00,4317,0,POZNAN GLOWNY,0,1\n"
            "1_765177_Y,05:43:00,05:43:00,3002,1,POZNAN GLOWNY,0,0\n"
            "1_765177_Y,06:30:00,06:30:00,1807,2,POZNAN GLOWNY,1,0\n"
        )
        return stops_csv, routes_csv, stop_times_csv

    def _mock_gtfs_zip(self) -> zipfile.ZipFile:
        stops_csv, routes_csv, stop_times_csv = self._mock_gtfs_payload()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("stops.txt", stops_csv)
            zf.writestr("routes.txt", routes_csv)
            zf.writestr("stop_times.txt", stop_times_csv)
        buffer.seek(0)
        return zipfile.ZipFile(buffer)