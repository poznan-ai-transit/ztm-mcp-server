# Document 4: MVP Limits (Supported Scope and Out of Scope)

## Purpose
This document states what `ztm-mcp-server` can and cannot do in the MVP, so that
users, agents and developers share the same expectations. It complements
`docs/02-mvp.md` (scope boundaries) and `docs/03-requirements.md` (requirements).

The same scope is sent to every connecting agent as MCP server instructions
(`SERVER_INSTRUCTIONS` in `src/mcp_server/instructions.py`). Keep the two in sync.
The promptfoo tests in `src/tests/promptfooconfig.yaml` hold a copy of that text.

## Quick summary for agents
- I answer questions about **scheduled public transport in Poznań (ZTM Poznań network)**.
- I can find stops and lines, list lines serving a stop, and give **scheduled** departures.
- I do **not** provide real-time data: no delays, no live vehicle positions, no service alerts.
- I do **not** plan routes (A to B), show maps, or give fares.
- I do **not** answer questions about other cities, intercity trains or non-transit topics.
- My timetable data is a **snapshot valid only for 2026-05-23 to 2026-05-27**.

## Supported

| Area | What works | MCP tool / resource |
|---|---|---|
| Stop lookup | Fuzzy search by name; tolerates typos, missing diacritics and partial names. Returns `stop_id` with a match `score`. | `search_stops` |
| Line lookup | Search by line number or route name. Returns `route_id` and the stop pattern per direction. | `search_routes` |
| Lines at a stop | All lines in the dataset that serve a given stop, regardless of date. | `get_routes_for_stop` |
| Scheduled departures | Up to 100 departures for one line at one stop, from a given time on a given service day. Optional direction (0 or 1). | `get_departures` |
| Full directory | All routes and all stops of the dataset in one payload. | resource `resource://ztm_static_storage/list_routes_and_stops` |

Details that apply to `get_departures`:
- Inputs: `stop_id`, `route_id`, `date` (`YYYY-MM-DD`), `time` (`HH:MM`), optional
  `direction_id` (0 or 1), `limit` (1 to 100, default 5).
- Stop and line IDs must be resolved first with `search_stops` and `search_routes`.
  The agent must not guess them.
- Date and time are required. The tools do not know the current date, so the agent must
  get it from the user or from its own context.
- Overnight trips can have times past 24:00. They belong to the service day they started on;
  `departure_datetime` shows the real calendar date.
- Every response is marked `"data_source": "static_mock"` and `"realtime": false`.

### Coverage of the bundled dataset
- 215 lines (192 bus, 23 tram), 3,251 stops, 38,639 trips.
- The network is ZTM Poznań **including municipalities around the city**. Stop names in
  those areas use the form `Place/Street`, for example `Luboń/Dworcowa`,
  `Mosina/Dworzec Kolejowy`, `Swarzędz/Dworzec Kolejowy`, `Kórnik/...`, `Puszczykowo/...`,
  `Rokietnica/...`, `M. Goślina/...`, `Tarnowo Pdg/...` and `Środa Wielkopolska/...`.
  Questions about these stops are in scope.

### Agent behaviour
See FR-1 to FR-4 in `docs/03-requirements.md` (FR-1 is refined below).

- **Missing details.** The agent asks one short question when a stop, line, date or time
  is missing, instead of calling a tool. It does not ask for confirmation of details the
  user already gave.
- **Ambiguous matches.** When a search returns several plausible results, the agent asks
  the user to choose.
- **No city named.** The agent assumes Poznań and answers as if the question concerned Poznań.
- **A name is not a location.** A stop or line name that contains another city's name
  (for example `Warszawa Centralna` or `Berlin Main station`) is never out of scope by
  itself. The agent checks it with the search tools and reports what they return. Stops
  named like other cities exist in Poznań (for example the street stop `Wrocławska`), and
  the dataset can change.
- **Explicit other city.** When the user explicitly asks about another city, for example
  Kraków, the agent declines, says that it does not answer questions about that city, and
  states that only Poznań timetables are supported. It does not call a tool.

## Out of scope for the MVP

| Not supported | What the agent should say or do |
|---|---|
| Real-time delays, "has the bus already left" | Say that only scheduled times are available. |
| Live vehicle positions and the vehicle catalog | Not available. The feeds are used only in `examples/`, not exposed as tools. |
| Service alerts, detours, disruptions | Not available. Suggest checking ZTM Poznań directly. |
| Route planning, transfers, "how do I get from A to B" | Not available. Offer lines at a stop or departures instead. |
| Maps and external map integration | Not available. |
| Accessibility information (low-floor vehicles, wheelchair flags) | Not available. Planned for Phase 2. |
| Fares and tickets | Not available. |
| Explicitly named other cities, intercity trains, other operators | Decline without calling a tool (see "Explicit other city"). |
| Dates outside the dataset validity | Say that the timetable data does not cover that date. |
| Questions unrelated to public transport | Politely decline without calling a tool. |

## Known limitations of the current implementation

1. **Data is a bundled snapshot, not live data.** `ZTMService.get_static_gtfs()` reads
   `src/mock_data/mock_data.zip`. The daily 06:00 refresh worker runs, but it reloads the
   same bundled file; `_fetch_static_gtfs_zip()` (live ZTM endpoint) is not called yet.
   FR-7 (daily refresh from the ZTM Open Data API) is therefore not fully implemented.
2. **Snapshot validity is 2026-05-23 to 2026-05-27** (`feed_info.txt`), five consecutive days
   (Saturday to Wednesday). Outside this period `get_departures` returns an empty list.
   The tool description says an empty list means "no matching departures", which an agent
   can wrongly read as "no service that day". The validity period is loaded (`FeedInfo`)
   but not exposed by any tool, and the server instructions tell the agent to explain
   this instead of claiming that no vehicles run.
3. **Scope is communicated, not enforced.** Server instructions tell the agent what is
   supported, but the server itself does not reject out-of-scope questions. Compliance
   depends on the model and is measured by the promptfoo tests, which are non-deterministic.
4. **Search is fuzzy.** Stop search returns matches with a score of at least 0.5, line
   search above 0.45. Low scores can be wrong stops, so ambiguous results must be
   confirmed with the user.
5. **The full directory resource is large** (all stops and routes) and is better avoided
   for simple questions.
6. **`echo` is a test tool** and is exposed by the server. It is not part of the product.

## Documentation to keep in sync
- `docs/02-mvp.md` describes "hardcoded tools" and "mock data". The code uses a real GTFS
  snapshot, not hardcoded values. Its "Supported Location" line should reflect the
  "Explicit other city" rule above.
- `docs/03-requirements.md`, FR-1, says the system must "reject queries for other cities".
  It should say that only an explicitly named other city is refused.
- `README.md`, "Documentation map": add a row for this document.