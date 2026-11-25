# MVP implementation status

## Purpose
This document describes the implemented MVP scope and its current limitations.
It complements `docs/02-mvp.md` (scope boundaries) and
`docs/03-requirements.md` (requirements).

The detailed MCP tool contract, including inputs, outputs and errors, is
documented in [`04-mcp-tool-contract.md`](./04-mcp-tool-contract.md).

## Supported

| Area | What works | MCP tool / resource |
|---|---|---|
| Stop lookup | Fuzzy search by name; tolerates typos, missing diacritics and partial names. Returns `stop_id` with a match `score`. | `search_stops` |
| Line lookup | Search by line number or route name. Returns `route_id` and the stop pattern per direction. | `search_routes` |
| Lines at a stop | All lines in the dataset that serve a given stop, regardless of date. | `get_routes_for_stop` |
| Scheduled departures | Scheduled departures for a stop and line. | `get_departures` |
| Scheduled journeys | Journeys between resolved stop IDs, with a maximum number of vehicle changes. | `search_connection` |
| Full directory | All routes and all stops of the dataset in one payload. | resource `resource://ztm_static_storage/list_routes_and_stops` |

## MVP boundaries

The MVP provides static timetable data for the ZTM Poznań network. It does not
provide real-time delays, vehicle positions, service alerts, walking routes,
maps or fares. Journey search supports transfers at the same `stop_id` only;
it does not model walking between platforms. Requests beyond these tools need
a short explanation of the limitation or another suitable source.

An assistant can combine scheduled journeys with external venue research to
plan free time or a shared café visit. Those workflows are documented in
[`06-use-cases.md`](./06-use-cases.md); venue search and meeting optimization
are not dedicated MCP tools.

### Agent behaviour
The agent:

- asks one concise question when required details are missing;
- considers all same-name platforms and clarifies genuinely different places by name;
- assumes Poznań when no city is specified;
- checks names containing another city's name instead of rejecting them automatically;
- declines requests explicitly about other cities without calling a tool.

These rules refine FR-1 to FR-4 in `docs/03-requirements.md`.
