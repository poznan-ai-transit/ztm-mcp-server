from __future__ import annotations

import csv
import io
import re
import threading
import zipfile
from _thread import LockType
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

from ini import ini
from logging_config import logger
from services.ztm_schedule_repository import WARSAW, ScheduleEntry, ScheduleRepository, warsaw_today
from services.ztm_static_schedule import ZTMStaticSchedule


@dataclass(frozen=True, slots=True)
class GTFSArchive:
    filename: str
    start_date: date
    end_date: date
    modified_at: datetime


class _ArchiveTableParser(HTMLParser):
    """Extract archive metadata from the published ZTM table, without extra dependencies."""

    def __init__(self) -> None:
        super().__init__()
        self.archives: list[GTFSArchive] = []
        self._cells: list[str] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cells = []
        elif tag == "td":
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None:
            self._cells.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._cells:
            match = re.fullmatch(r"(\d{8})_(\d{8})\.zip", self._cells[0])
            if match is None:
                return
            if len(self._cells) < 3:
                raise ValueError(f"Missing metadata for GTFS archive {self._cells[0]}")
            start = datetime.strptime(match[1], "%Y%m%d").date()
            end = datetime.strptime(match[2], "%Y%m%d").date()
            if start > end:
                raise ValueError(f"Invalid date range in GTFS archive {self._cells[0]}")
            modified = datetime.strptime(
                " ".join(self._cells[2].split()), "%d.%m.%Y, %H:%M:%S"
            ).replace(tzinfo=WARSAW)
            self.archives.append(GTFSArchive(self._cells[0], start, end, modified))


class MissingGTFSArchiveError(FileNotFoundError):
    def __init__(self, archive_path: Path) -> None:
        super().__init__(f"GTFS archive is missing: {archive_path}")
        self.archive_path = archive_path


class MissingGTFSFileError(FileNotFoundError):
    def __init__(self, filename: str) -> None:
        super().__init__(f"GTFS file is missing from archive: {filename}")
        self.filename = filename


class ZTMService:
    _instance = None
    _instance_lock: LockType = threading.Lock()

    @classmethod
    def instance(cls) -> ZTMService:
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
        self.repository = ScheduleRepository()
        self._initialized = True

    def start_daily_refresh(self) -> None:
        """Load initial schedules before returning, then refresh daily at 6 AM."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Daily refresh is already running")

        self._stop_event.clear()
        logger.info("Loading initial GTFS schedules before accepting MCP requests")
        self._refresh_schedules()

        def _runner() -> None:
            backoff = ini.refresh_retry_initial_seconds
            delay = self._seconds_until_next_refresh(datetime.now(WARSAW))
            logger.info("Initial GTFS schedules ready; next refresh in %.0f seconds", delay)
            self._stop_event.wait(timeout=delay)
            while not self._stop_event.is_set():
                try:
                    logger.info("Refreshing static GTFS schedules")
                    self._refresh_schedules()
                    backoff = ini.refresh_retry_initial_seconds
                    delay = self._seconds_until_next_refresh(datetime.now(WARSAW))
                    logger.info("GTFS refresh completed; next refresh in %.0f seconds", delay)
                    self._stop_event.wait(timeout=delay)
                except Exception:
                    if self._stop_event.is_set():
                        return
                    logger.exception("GTFS refresh failed; retrying in %s seconds", backoff)
                    self._stop_event.wait(timeout=backoff)
                    backoff = min(backoff * 2, ini.refresh_retry_max_seconds)

        self._thread = threading.Thread(
            target=_runner, name="ztm-static-schedule-refresh", daemon=True
        )
        self._thread.start()
        logger.info("Daily GTFS refresh worker started")

    def stop_daily_refresh(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            logger.debug("Waiting for GTFS refresh worker to stop")
            self._thread.join()
            self._thread = None
            logger.info("Daily GTFS refresh worker stopped")

    def list_gtfs_archives(self) -> list[GTFSArchive]:
        response = requests.get(ini.gtfs_archives_url, timeout=ini.request_timeout_seconds)
        response.raise_for_status()
        parser = _ArchiveTableParser()
        parser.feed(response.text)
        parser.close()
        if not parser.archives:
            raise ValueError("No GTFS archives found on the ZTM archive page")
        return sorted(parser.archives, key=lambda item: (item.modified_at, item.filename))

    def _refresh_schedules(self, today: date | None = None) -> None:
        """Publish all feeds reaching the rolling cutoff, including future feeds.

        Filenames prefilter downloads; actual GTFS metadata determines retained
        coverage. Any failure leaves the previous repository untouched.
        """
        today = today or warsaw_today()
        cutoff = today - timedelta(days=ini.gtfs_history_days)
        existing = {entry.filename: entry for entry in self.repository.list_schedules()}
        entries: list[ScheduleEntry] = []
        for archive in self.list_gtfs_archives():
            if archive.end_date < cutoff:
                continue
            if threading.current_thread() is self._thread and self._stop_event.is_set():
                return
            entry = existing.get(archive.filename)
            if entry is None or entry.modified_at != archive.modified_at:
                logger.info("Loading GTFS archive %s", archive.filename)
                schedule = ZTMStaticSchedule.load(self.get_static_gtfs(archive.filename))
                entry = ScheduleEntry(archive.filename, archive.modified_at, schedule)
            if entry.date_range[1] >= cutoff:
                entries.append(entry)

        # Require continuous recent coverage before publishing or pruning anything.
        candidate = ScheduleRepository(entries)
        day = cutoff
        while day <= today:
            candidate.for_date(day)
            day += timedelta(days=1)
        self.repository.replace(candidate.list_schedules())
        logger.info("Published %d independent GTFS schedules", len(entries))

    def get_static_gtfs(self, filename: str | None = None) -> dict[str, Any]:
        logger.debug("Reading GTFS archive %s", filename or "from default endpoint")
        with self._fetch_static_gtfs_zip(ini.gtfs_endpoint, filename=filename) as zf:
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
            "calendar_dates": calendar_dates,
        }

    def _read_csv_from_zip(self, zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
        try:
            with zf.open(filename) as f:
                text: str = f.read().decode("utf-8-sig")
        except KeyError as exc:
            raise MissingGTFSFileError(filename) from exc
        reader: csv.DictReader[str] = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        logger.debug("Read %s rows from %s", len(rows), filename)
        return rows

    def _seconds_until_next_six_am(self, now: datetime) -> float:
        next_run: datetime = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        return (next_run - now).total_seconds()

    def _seconds_until_next_refresh(self, now: datetime) -> float:
        six_am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if six_am <= now:
            six_am += timedelta(days=1)
        # Timestamps account for Warsaw DST transitions when computing the wait.
        return six_am.timestamp() - now.timestamp()

    def _fetch_static_gtfs_zip(
        self, gtfs_endpoint: str | None = None, *, filename: str | None = None
    ) -> zipfile.ZipFile:
        options: dict[str, Any] = {}
        if filename is not None:
            options["params"] = {"file": filename}
        resp: requests.Response = requests.get(
            gtfs_endpoint if gtfs_endpoint is not None else ini.gtfs_endpoint,
            timeout=ini.request_timeout_seconds,
            **options,
        )
        resp.raise_for_status()
        return zipfile.ZipFile(io.BytesIO(resp.content))

    def _mock_gtfs_zip(self) -> zipfile.ZipFile:
        zip_path: Path = Path(__file__).resolve().parents[1] / "mock_data" / "mock_data.zip"
        try:
            return zipfile.ZipFile(zip_path)
        except FileNotFoundError as exc:
            raise MissingGTFSArchiveError(zip_path) from exc
