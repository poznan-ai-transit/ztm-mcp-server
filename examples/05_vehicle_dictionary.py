"""
Fetch the ZTM Poznań vehicle catalog and join it with live vehicle positions.

The catalog (vehicle_dictionary.csv) covers a subset of the fleet. The join
key is vehicle.id from the positions feed matched against the 'vehicle' column
in the CSV. Vehicles not in the catalog are also shown for completeness.

Requires: requests, gtfs-realtime-bindings
    pip install requests gtfs-realtime-bindings
"""

import csv
import io

import requests
from google.transit import gtfs_realtime_pb2

BASE_URL = "https://www.ztm.poznan.pl"
RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"


def fetch_vehicle_catalog() -> dict[str, dict]:
    resp = requests.get(RT_ENDPOINT, params={"file": "vehicle_dictionary.csv"}, timeout=10)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    if not rows:
        return {}
    print("Vehicle catalog columns:", list(rows[0].keys()))
    return {row["vehicle"]: row for row in rows}


def fetch_vehicle_positions() -> gtfs_realtime_pb2.FeedMessage:
    resp = requests.get(RT_ENDPOINT, params={"file": "vehicle_positions.pb"}, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def main():
    print("Fetching vehicle catalog…")
    catalog = fetch_vehicle_catalog()
    print(f"Catalog entries: {len(catalog)}")

    print("\nFetching live vehicle positions…")
    feed = fetch_vehicle_positions()
    vehicles = [e for e in feed.entity if e.HasField("vehicle")]
    print(f"Live vehicles  : {len(vehicles)}")

    matched = [e for e in vehicles if e.vehicle.vehicle.id in catalog]
    unmatched = [e for e in vehicles if e.vehicle.vehicle.id not in catalog]
    print(f"Matched to catalog: {len(matched)}  |  Not in catalog: {len(unmatched)}")

    print("\nFirst 10 vehicles matched to catalog:")
    for entity in matched[:10]:
        v = entity.vehicle
        vid = v.vehicle.id
        meta = catalog[vid]
        pos = v.position
        print(
            f"  vehicle={vid:<10}  "
            f"route={v.trip.route_id:<6}  "
            f"pos=({pos.latitude:.5f}, {pos.longitude:.5f})  "
            f"ramp={meta['ramp']}  "
            f"low_floor={meta['hf_lf_le']}  "
            f"ac={meta['air_conditioner']}"
        )

    if unmatched:
        print(f"\nFirst 3 vehicles NOT in catalog (id not in vehicle_dictionary.csv):")
        for entity in unmatched[:3]:
            v = entity.vehicle
            pos = v.position
            print(
                f"  vehicle={v.vehicle.id:<10}  "
                f"route={v.trip.route_id:<6}  "
                f"pos=({pos.latitude:.5f}, {pos.longitude:.5f})"
            )


if __name__ == "__main__":
    main()
