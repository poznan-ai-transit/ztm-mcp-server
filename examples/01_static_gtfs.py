"""
Fetch the latest ZTM Poznań static GTFS archive and print a sample of stops.

Requires: requests
"""

import io
import zipfile

import requests

BASE_URL = "https://www.ztm.poznan.pl"
GTFS_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGTFSFile"


def fetch_gtfs_zip() -> zipfile.ZipFile:
    resp = requests.get(GTFS_ENDPOINT, timeout=30)
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def read_csv_from_zip(zf: zipfile.ZipFile, filename: str) -> list[dict]:
    import csv

    with zf.open(filename) as f:
        text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def main():
    print("Downloading latest GTFS archive…")
    zf = fetch_gtfs_zip()

    print("Files in archive:", zf.namelist())

    stops = read_csv_from_zip(zf, "stops.txt")
    print(f"\nTotal stops: {len(stops)}")
    print("\nFirst 5 stops:")
    for stop in stops[:5]:
        print(
            f"  id={stop['stop_id']:>6}  "
            f"lat={stop['stop_lat']}  "
            f"lon={stop['stop_lon']}  "
            f"name={stop['stop_name']}"
        )

    routes = read_csv_from_zip(zf, "routes.txt")
    print(f"\nTotal routes: {len(routes)}")
    print("\nFirst 5 routes:")
    for route in routes[:5]:
        print(
            f"  id={route['route_id']:>6}  "
            f"short={route.get('route_short_name', '?'):>4}  "
            f"long={route.get('route_long_name', '?')}"
        )


if __name__ == "__main__":
    main()
