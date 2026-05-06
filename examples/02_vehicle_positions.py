"""
Fetch live vehicle positions from ZTM Poznań GTFS-RT feed and print them.

Requires: requests, gtfs-realtime-bindings
    pip install requests gtfs-realtime-bindings
"""

import requests
from google.transit import gtfs_realtime_pb2

BASE_URL = "https://www.ztm.poznan.pl"
RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"


def fetch_vehicle_positions() -> gtfs_realtime_pb2.FeedMessage:
    resp = requests.get(RT_ENDPOINT, params={"file": "vehicle_positions.pb"}, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def main():
    feed = fetch_vehicle_positions()

    import datetime
    ts = datetime.datetime.fromtimestamp(feed.header.timestamp)
    print(f"Feed timestamp : {ts}")
    print(f"Total entities : {len(feed.entity)}")

    vehicles = [e for e in feed.entity if e.HasField("vehicle")]
    print(f"Vehicle records: {len(vehicles)}")

    print("\nFirst 10 vehicles:")
    for entity in vehicles[:10]:
        v = entity.vehicle
        pos = v.position
        print(
            f"  vehicle={v.vehicle.id:<10}  "
            f"trip={v.trip.trip_id:<20}  "
            f"route={v.trip.route_id:<6}  "
            f"pos=({pos.latitude:.5f}, {pos.longitude:.5f})  "
            f"bearing={pos.bearing:>3.0f}°"
        )


if __name__ == "__main__":
    main()
