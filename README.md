# ztm-mcp-server

An MCP server that lets AI assistants answer Poznań public transport questions using ZTM GTFS timetables. Latest `origin/main` also enriches departures with GTFS-RT delays, predicted times and cancellations when available; journey search remains timetable-based.

The capability descriptions below reflect `origin/main` at `da7ce68`, checked
on 25 November 2026. This use-case branch has not incorporated that runtime code.

## Use cases

These examples reflect conversations tried in this project:

| Ask the assistant | What it does |
|---|---|
| “Find Rondo Kaponiera stops.” / “Znajdź przystanek Rondo Kaponiera.” | Finds matching platforms; follow-ups can reuse the stop context. |
| “Which lines serve those stops?” | Checks every matching platform and combines the line lists. |
| “Show the next five departures of line 15 there today after 14:00.” | Resolves the line and returns scheduled departures across applicable platforms. |
| “Is my next line 15 tram delayed or cancelled?” | Checks departure updates and reports available predictions, delay and cancellation status; explains when no real-time update is available. |
| “Get me from Poznań Główny to Rondo Kaponiera after 14:00, with at most one change.” | Searches scheduled journeys and shows boarding points, directions, arrival times and transfers. |
| “I finish at Politechnika at 11:00 and need to be at Krauthofera by 12:30. What can I do on the way?” | Combines scheduled transit legs with activity research, walking estimates and an arrival buffer. |
| “I'm near Rondo Kaponiera; my friend is at Politechnika. Find a café we can both reach around 17:00 without changing vehicles.” | Compares direct journeys from both origins with externally researched cafés. |

The last two are assistant workflows: venue details and walking estimates come
from other sources, while this MCP supplies scheduled journeys and optional
real-time departure updates. All times use Europe/Warsaw. Journey search does
not account for live delays or walking transfers. Resolve
“today” when running a prompt; historical session times are not current advice.

See [detailed use cases](docs/06-use-cases.md) and the
[use case diagram](docs/diagrams/ztm-mcp-use-case.svg).

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

## Running the MCP server

```bash
python src/main.py
```

Test with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

- Transport Type: **Streamable HTTP**
- URL: `http://localhost:8000/mcp`

Stateless HTTP is enabled to avoid missing session ID errors.

## Docker

```bash
docker compose up --build -d  # start
docker compose down          # stop
docker compose logs mcp      # HTTP logs
docker compose exec mcp tail -n 50 /app/_logs_/server.log
```

External access (`.env`):

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8000
```

## Demo running

```bash
pip install -r requirements-demo.txt
streamlit run src/demo.py
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

## Tests

```bash
pip install -r requirements-dev.txt
pytest                 # full suite
pytest -m "not slow"   # skip the tests that parse/freeze the full mock dataset
```

## Linting & formatting

Code style is enforced with [Ruff](https://docs.astral.sh/ruff/) (configured in
[pyproject.toml](pyproject.toml)):

```bash
ruff check src tests           # lint
ruff format src tests          # auto-format
ruff check --fix src tests     # lint + apply safe fixes
```

Both linting and the test suite run in CI on every push and pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

Tests live in [tests/](tests/) and cover the in-memory schedule store
([test_ztm_static_schedule.py](tests/test_ztm_static_schedule.py)), the ZTM data
service ([test_ztm_service.py](tests/test_ztm_service.py)), and the MCP tool and
resource callables ([test_mcp_server.py](tests/test_mcp_server.py)). They run
entirely against the bundled mock data — no network access required.

## Documentation map

| Document | Purpose |
|---|---|
| [docs/01-product-goal.md](docs/01-product-goal.md) | Project description and product goal |
| [docs/02-mvp.md](docs/02-mvp.md) | MVP boundaries, mock phase, and core behavior |
| [docs/03-requirements.md](docs/03-requirements.md) | Functional and non-functional requirements for the MVP |
| [docs/04-mcp-tool-contract.md](docs/04-mcp-tool-contract.md) | Tool inputs, outputs, JSON examples, identifier resolution, and errors |
| [docs/05-mvp-implementation.md](docs/05-mvp-implementation.md) | Implemented capabilities and limitations |
| [docs/06-use-cases.md](docs/06-use-cases.md) | Session-based scenarios, expected behavior, and assistant integrations |
