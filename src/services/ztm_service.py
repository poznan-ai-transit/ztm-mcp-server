from __future__ import annotations

from _thread import LockType
import csv
import io
from pathlib import Path
import threading
import zipfile
from datetime import datetime, timedelta
from typing import Any
import requests

from services.ztm_static_schedule import ZTMStaticSchedule


class ZTMService:
    _instance = None
    _instance_lock: LockType = threading.Lock()

    @classmethod
    def instance(cls) -> "ZTMService":
        return cls()

    def __new__(cls) -> ZTMService:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._initialized = True

    def start_daily_refresh(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Daily refresh is already running")

        self._stop_event.clear()
        storage: ZTMStaticSchedule = ZTMStaticSchedule.instance()

        def _runner() -> None:
            backoff = 60
            while not self._stop_event.is_set():
                try:
                    storage.set_static_gtfs(self.get_static_gtfs())
                    backoff = 60
                    self._stop_event.wait(timeout=self._seconds_until_next_six_am(datetime.now()))
                except Exception:
                    # log exception
                    self._stop_event.wait(timeout=backoff)
                    backoff: int = min(backoff * 2, 1800)

        self._thread = threading.Thread(target=_runner, name="ztm-static-schedule-refresh", daemon=True)
        self._thread.start()

    def stop_daily_refresh(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def get_static_gtfs(self) -> dict[str, Any]:
        with self._mock_gtfs_zip() as zf:
            stops: list[dict[str, str]] = self._read_csv_from_zip(zf, "stops.txt")
            routes: list[dict[str, str]] = self._read_csv_from_zip(zf, "routes.txt")
            stop_times: list[dict[str, str]] = self._read_csv_from_zip(zf, "stop_times.txt")
            trips: list[dict[str, str]] = self._read_csv_from_zip(zf, "trips.txt")
            calendar: list[dict[str, str]] = self._read_csv_from_zip(zf, "calendar.txt")
            feed_info: list[dict[str, str]] = self._read_csv_from_zip(zf, "feed_info.txt")
            calendar_dates: list[dict[str, str]] = self._read_csv_from_zip(zf, "calendar_dates.txt")
            

        return {
            "stops": stops,
            "routes": routes,
            "stop_times": stop_times,
            "trips": trips,
            "calendar": calendar,
            "feed_info": feed_info,
            "calendar_dates": calendar_dates
        }

    def _read_csv_from_zip(self, zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
        with zf.open(filename) as f:
            text: str = f.read().decode("utf-8-sig")
        reader: csv.DictReader[str] = csv.DictReader(io.StringIO(text))
        return [row for row in reader]

    def _seconds_until_next_six_am(self, now: datetime) -> float:
        next_run: datetime = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        return (next_run - now).total_seconds()

    def _fetch_static_gtfs_zip(self, gtfs_endpoint: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile") -> zipfile.ZipFile:
        resp: requests.Response = requests.get(gtfs_endpoint, timeout=30)
        resp.raise_for_status()
        return zipfile.ZipFile(io.BytesIO(resp.content))

    def _mock_gtfs_zip(self) -> zipfile.ZipFile:
        zip_path: Path = Path(__file__).resolve().parents[1] / "mock_data" / "mock_data.zip"
        return zipfile.ZipFile(zip_path)