from __future__ import annotations

import csv
import io
from pathlib import Path
import threading
import time
import zipfile
from datetime import datetime, timedelta
from typing import Any
import requests

from services.static_storage import WriteOnlyStaticStorage

class ZTMService:
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ZTMService":
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
        self._initialized = True

    def start_daily_refresh(self, storage: WriteOnlyStaticStorage) -> None:
        def _runner():
            backoff = 60
            while True:
                try:
                    storage.set_static_gtfs(self.get_static_gtfs())
                    backoff = 60
                    time.sleep(self._seconds_until_next_six_am(datetime.now()))
                except Exception:
                    # log exception
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 1800)

        thread = threading.Thread(target=_runner, name="ztm-static-refresh", daemon=True)
        thread.start()

    def get_static_gtfs(self) -> dict[str, Any]:
        # mock data in the same shape as the real API (zip with GTFS files)
        
        with self._mock_gtfs_zip() as zf:
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
    
    
    def _fetch_static_gtfs_zip(self, gtfs_endpoint: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile") -> zipfile.ZipFile:
        resp = requests.get(gtfs_endpoint, timeout=30)
        resp.raise_for_status()
        return zipfile.ZipFile(io.BytesIO(resp.content))

    def _mock_gtfs_zip(self) -> zipfile.ZipFile:
        zip_path = Path(__file__).resolve().parents[1] / "mock_data" / "mock_data.zip"
        return zipfile.ZipFile(zip_path)