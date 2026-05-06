"""
Fetch GTFS-RT trip updates (delays) and show predicted arrival times.

The example joins against the static GTFS stop_times.txt to compute
the predicted departure time for each stop with a delay report.

Requires: requests, gtfs-realtime-bindings
    pip install requests gtfs-realtime-bindings
"""

import csv
import datetime
import io
import zipfile
from collections import defaultdict

import requests
from google.transit import gtfs_realtime_pb2

BASE_URL = "https://www.ztm.poznan.pl"
RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"
GTFS_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGTFSFile"

# GTFS encodes post-midnight times as 25:xx:xx — convert to seconds from midnight.
def gtfs_time_to_seconds(t: str) -> int:
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


def seconds_to_time(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fetch_static_stop_times() -> dict[str, list[dict]]:
    """Return stop_times grouped by trip_id."""
    resp = requests.get(GTFS_ENDPOINT, timeout=30)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    with zf.open("stop_times.txt") as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    by_trip: dict[str, list[dict]] = defaultdict(list)
    for row in reader:
        by_trip[row["trip_id"]].append(row)
    return by_trip


def fetch_trip_updates() -> gtfs_realtime_pb2.FeedMessage:
    resp = requests.get(RT_ENDPOINT, params={"file": "trip_updates.pb"}, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def main():
    print("Downloading static stop times (this may take a moment)…")
    stop_times = fetch_static_stop_times()

    print("Fetching trip updates…")
    feed = fetch_trip_updates()

    updates = [e for e in feed.entity if e.HasField("trip_update")]
    print(f"Trip update entities: {len(updates)}")

    # Show first 5 trips that have delay info
    shown = 0
    for entity in updates:
        if shown >= 5:
            break
        tu = entity.trip_update
        trip_id = tu.trip.trip_id

        if not tu.stop_time_update:
            continue

        print(f"\nTrip {trip_id} (route {tu.trip.route_id}):")
        for stu in tu.stop_time_update[:3]:
            delay = stu.departure.delay if stu.HasField("departure") else (
                stu.arrival.delay if stu.HasField("arrival") else None
            )
            if delay is None:
                continue

            # Look up scheduled time from static feed
            static_rows = {r["stop_sequence"]: r for r in stop_times.get(trip_id, [])}
            seq = str(stu.stop_sequence)
            scheduled_str = static_rows.get(seq, {}).get("departure_time", "?")

            if scheduled_str != "?":
                scheduled_secs = gtfs_time_to_seconds(scheduled_str)
                predicted_secs = scheduled_secs + delay
                predicted_str = seconds_to_time(predicted_secs)
            else:
                predicted_str = "?"

            print(
                f"  stop_seq={stu.stop_sequence:<4}  "
                f"stop_id={stu.stop_id:<8}  "
                f"delay={delay:>+5}s  "
                f"scheduled={scheduled_str}  "
                f"predicted={predicted_str}"
            )
        shown += 1


if __name__ == "__main__":
    main()
