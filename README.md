# ztm-mcp-server

Example scripts for the **ZTM Poznań Open Data API** — public transport feeds (GTFS + GTFS-RT) published by Zarząd Transportu Miejskiego w Poznaniu.

## Requirements

- **Python 3.10+**
- `requests` — HTTP client
- `gtfs-realtime-bindings` — Protocol Buffer parser for GTFS-RT feeds (pulls in `protobuf` as a transitive dependency)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Examples

| Script | What it does |
|---|---|
| [examples/01_static_gtfs.py](examples/01_static_gtfs.py) | Download the latest GTFS archive and print stops & routes |
| [examples/02_vehicle_positions.py](examples/02_vehicle_positions.py) | Fetch live vehicle positions (lat/lon, bearing, trip, route) |
| [examples/03_trip_updates.py](examples/03_trip_updates.py) | Fetch real-time delays and compute predicted departure times |
| [examples/04_service_alerts.py](examples/04_service_alerts.py) | List active service disruptions, detours, and notices |
| [examples/05_vehicle_dictionary.py](examples/05_vehicle_dictionary.py) | Join the vehicle catalog (accessibility flags) with live positions |

Run any example:

```bash
python examples/01_static_gtfs.py
python examples/02_vehicle_positions.py
python examples/03_trip_updates.py
python examples/04_service_alerts.py
python examples/05_vehicle_dictionary.py
```

## API overview

| Feed | Format | File |
|---|---|---|
| Static schedule | GTFS ZIP | `getGTFSFile` |
| Service alerts | GTFS-RT protobuf | `feeds.pb` |
| Trip delays | GTFS-RT protobuf | `trip_updates.pb` |
| Vehicle positions | GTFS-RT protobuf | `vehicle_positions.pb` |
| Vehicle catalog | CSV | `vehicle_dictionary.csv` |

Base URL: `https://www.ztm.poznan.pl/pl/dla-deweloperow/`

Data source: [ZTM Poznań developer portal](https://www.ztm.poznan.pl/otwarte-dane/dla-deweloperow/)

## Documentation map

| Document | Purpose |
|---|---|
| [docs/01-product-goal.md](docs/01-product-goal.md) | Project description and product goal |
| [docs/02-mvp.md](docs/02-mvp.md) | MVP boundaries, mock phase, and core behavior |
| [docs/03-requirements.md](docs/03-requirements.md) | Functional and non-functional requirements for the MVP |
