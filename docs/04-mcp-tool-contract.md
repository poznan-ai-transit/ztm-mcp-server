# MCP tool contract

The tools in `src/mcp_server/tools.py` serve static timetables
GTFS data. They provide no live delays, predictions, or vehicle status.
Response types are defined in `src/mcp_server/schemas.py`; results below exclude
MCP transport wrappers. The server supplies `ctx`; never include it in arguments.

## Tool list

| Tool | Purpose |
|---|---|
| [`search_stops`](#search_stops) | Find stop IDs by name. |
| [`search_routes`](#search_routes) | Find route IDs by line number, name, or ID. |
| [`get_routes_for_stop`](#get_routes_for_stop) | List routes serving a stop. |
| [`get_departures`](#get_departures) | Get scheduled departures for a stop, route, and service day. |
| [`echo`](#echo) | Return text for diagnostics. |

## Shared rules

- Obtain required details from the conversation. Ask one concise question for
  missing details and wait; clarify ambiguous search matches before selecting IDs.
- Resolve `stop_id` with `search_stops` and `route_id` with `search_routes`.
  Reuse resolved IDs when applicable; never invent them.
- Pass IDs unchanged as JSON strings, preserving leading zeros. A `stop_id` is
  not a stop name, public `stop_code`, list index, or `stop_sequence`;
  `route_short_name` (the displayed line) is not a `route_id`.
- All `limit` arguments accept integers from `1` to `100`.
- Searches return matches by descending `score` (`0`–`1`, higher is better,
  not a probability), or `[]` when none match. Searches and route listings are
  independent of operating dates and do not guarantee service on a given day.

## `search_stops`

Search stop names using normalized exact, partial, and fuzzy matching.

| Input | Type | Requirement |
|---|---|---|
| `query` | string | Required nonblank stop name. |
| `limit` | integer | Optional; default `10`. |

Returns an array of:

| Field | Type | Meaning |
|---|---|---|
| `stop_id` | string | GTFS ID for subsequent calls. |
| `stop_code` | string | Public stop code. |
| `stop_name` | string | Display name. |
| `stop_lat`, `stop_lon` | number | Coordinates in degrees. |
| `zone_id` | string | Fare zone. |
| `score` | number | Match score. |

## `search_routes`

Search by line number, route name, or route ID.

| Input | Type | Requirement |
|---|---|---|
| `query` | string | Required nonblank line number, name, or ID. |
| `limit` | integer | Optional; default `10`. |

Returns an array of:

| Field | Type | Meaning |
|---|---|---|
| `route_id` | string | GTFS ID for subsequent calls. |
| `route_short_name`, `route_long_name` | string | Displayed line and full route name. |
| `route_type` | integer | GTFS transport type, e.g. `3` for bus. |
| `directions` | object | Direction keys (`"0"`, `"1"`) mapped to ordered stop-name arrays; representative patterns, not individual trip itineraries. |
| `score` | number | Match score. |

## `get_routes_for_stop`

List routes serving a stop, regardless of operating date. No line, date, or time
is required.

| Input | Type | Requirement |
|---|---|---|
| `stop_id` | string | Required resolved stop ID. |

Returns an array sorted lexicographically by `route_id`, with string fields
`route_id`, `agency_id`, `route_short_name`, `route_long_name`, and integer
`route_type`. It omits `directions` and `score`. A known stop with no routes
returns `[]`; an unknown stop raises `ToolError`.

## `get_departures`

Return scheduled departures for a stop, route, and GTFS service day.

| Input | Type | Requirement |
|---|---|---|
| `stop_id` | string | Required resolved stop ID. |
| `route_id` | string | Required resolved route ID. |
| `date` | string | Required service date, `YYYY-MM-DD`. |
| `time` | string | Required inclusive lower bound, `HH:MM` (`00:00`–`23:59`). |
| `direction_id` | integer or null | Optional: `0` or `1`; omitted/null means both. |
| `limit` | integer | Optional; default `5`. |

Returns an object with:

| Field | Type | Meaning |
|---|---|---|
| `stop_id`, `stop_name` | string | Selected stop ID and name. |
| `route_id`, `route_short_name` | string | Selected route ID and displayed line. |
| `date`, `time` | string | Requested service date and time. |
| `direction_id` | integer or null | Requested direction filter. |
| `realtime` | boolean | Always `false`. |
| `departures` | array | Matches ordered by scheduled departure time. |

Each departure contains:

| Field | Type | Meaning |
|---|---|---|
| `departure_time` | string | GTFS `HH:MM:SS`; hours may exceed `23`. |
| `departure_datetime` | string | ISO calendar datetime in Europe/Warsaw local time, without a UTC offset. |
| `trip_id`, `route_id`, `route_short_name` | string | Trip ID, route ID, and displayed line. |
| `trip_headsign` | string | Destination/headsign. |
| `direction_id` | integer | Trip direction. |
| `stop_sequence` | integer | Position in the trip. |

### Service-day behavior

`date` selects one GTFS service day, respecting weekly calendars and date
exceptions. For `2026-06-15`, departure `25:30:00` means
`2026-06-16T01:30:00`. Input times also use Europe/Warsaw local time; do not
interpret returned datetimes as UTC. The tool neither includes the previous
service day's overnight trips nor advances to the next service day. Dates
outside the loaded dataset's supported range are rejected.

An empty `departures` array retains all response metadata and means no matching
service at or after `time`: the route may not serve the stop, date, or direction.
Unknown IDs are errors. Static results never imply zero delay or live status.

## `echo`

Required input `text` (string); returns the same string, including empty text
and Polish characters.

## Errors

Invalid arguments raise MCP `ToolError`, not response objects with an `error`
field. Missing arguments and incompatible types use framework validation;
its exact messages are unspecified.

| Condition | Error message |
|---|---|
| Blank search query | `Invalid query. Provide a non-empty stop or route name/number.` |
| Invalid limit | `Invalid limit. Use an integer from 1 to 100.` |
| Wrong date format | `Invalid date. Use YYYY-MM-DD.` |
| Impossible date | `Invalid date. Provide a valid calendar date as YYYY-MM-DD.` |
| Date outside dataset range | Error includes the supported date range. |
| Invalid time | `Invalid time. Use HH:MM from 00:00 through 23:59.` |
| Invalid direction | `Invalid direction_id. Use 0 or 1, or omit it.` |
| Unknown stop | `Unknown stop_id. Use search_stops to find a valid stop.` |
| Unknown route | `Unknown route_id. Use search_routes to find a valid line.` |
