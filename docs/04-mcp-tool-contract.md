# MCP tool contract

This document describes the current tools in `src/mcp_server/tools.py`.
The MVP serves static timetables from the bundled mock GTFS archive. Live
delays, predicted departures, and GTFS-RT trip updates are outside this contract.
`examples/01_static_gtfs.py` illustrates the source stop and route fields;
`examples/03_trip_updates.py` is a standalone example, not an MCP tool.

JSON outputs below show the logical tool result, without MCP transport wrappers.
Examples use the synthetic IDs and data from `tests/conftest.py`; these are not
IDs to copy into requests against the bundled dataset. `ctx` is supplied by the
server and must not be included in tool arguments. Current return annotations
use generic dictionaries; this document describes their fields but does not add
typed output models or change runtime validation.

## Resolving identifiers

1. Obtain the stop name from the conversation. If missing, ask and wait.
2. Call `search_stops` with that name.
3. If several matches could be intended, ask the user to choose before proceeding.
4. Copy the selected result's `stop_id` exactly, as a JSON string, into
   `get_routes_for_stop` or `get_departures`. Reuse a previously resolved ID when
   applicable; never invent one.

`stop_id` is the identifier from GTFS `stops.txt`. It is not `stop_name`,
`stop_code`, a list index, or `stop_sequence`. Preserve leading zeros and other
characters; do not convert IDs to numbers. Similarly, obtain `route_id` through
`search_routes`; the displayed line number (`route_short_name`) is a separate
field and must not be substituted for it.

Example sequence of tool arguments:

```json
{"query": "Żabinko"}
```

After selecting the returned stop with `stop_id: "S2"`:

```json
{"stop_id": "S2"}
```

For departures, also resolve the line through `search_routes` and obtain the
service date and time from the conversation. Ask one concise question for any
missing required details and wait; do not guess. Direction and limit are optional.

## `search_stops`

Search stop names using normalized exact, partial, and fuzzy matching.

| Input | JSON type | Required | Meaning |
|---|---|---|---|
| `query` | string | yes | Stop name supplied in the conversation; do not send an empty query. |
| `limit` | integer | no | Maximum results; default `10`. Use a positive integer. |

Returns an array sorted by descending match score. Each item contains:

| Field | JSON type | Meaning |
|---|---|---|
| `stop_id` | string | Exact GTFS identifier to pass to subsequent tools. |
| `stop_code` | string | Public stop code; separate from `stop_id`. |
| `stop_name` | string | Display name. |
| `stop_lat`, `stop_lon` | number | Geographic coordinates in degrees. |
| `zone_id` | string | Fare zone identifier. |
| `score` | number | Match score from `0` to `1`; higher is better, not a probability. |

Input:

```json
{"query": "Żabinko", "limit": 1}
```

Output:

```json
[
  {
    "stop_id": "S2",
    "stop_code": "002",
    "stop_name": "Żabinko",
    "stop_lat": 52.41,
    "stop_lon": 16.94,
    "zone_id": "A",
    "score": 1.0
  }
]
```

No matches returns `[]`. An empty or whitespace-only query also currently
returns `[]`, although agents are instructed to ask for a name instead.
The implementation does not explicitly validate search limit bounds; positive
limits are the caller convention, not an enforced range.

## `search_routes`

Search by line number, route name, or route ID. Resolve ambiguous matches with
the user before selecting an ID.

| Input | JSON type | Required | Meaning |
|---|---|---|---|
| `query` | string | yes | Line number or route name from the conversation. |
| `limit` | integer | no | Maximum results; default `10`. Use a positive integer. |

Returns an array sorted by descending match score. Each item contains:

| Field | JSON type | Meaning |
|---|---|---|
| `route_id` | string | Exact identifier for subsequent calls. |
| `route_short_name` | string | Displayed line number/name. |
| `route_long_name` | string | Full route name. |
| `route_type` | integer | GTFS transport type, e.g. `3` for bus. |
| `directions` | object | Direction ID keys (`"0"`, `"1"`) mapped to ordered arrays of stop names; representative stop patterns, not individual trip itineraries. |
| `score` | number | Match score from `0` to `1`; higher is better. |

Input:

```json
{"query": "R2", "limit": 1}
```

Output:

```json
[
  {
    "route_id": "R2",
    "route_short_name": "2",
    "route_long_name": "Śrem",
    "route_type": 3,
    "directions": {"1": ["Żabinko", "Dworzec"]},
    "score": 1.0
  }
]
```

No matches or an empty normalized query returns `[]`. As with `search_stops`,
search limit bounds are not explicitly validated. Neither search requires a
date or guarantees that the returned stop or route has service on a given date.

## `get_routes_for_stop`

List all routes serving a known stop in the dataset, regardless of operating
date. No line, date, or time is required.

| Input | JSON type | Required | Meaning |
|---|---|---|---|
| `stop_id` | string | yes | Exact ID resolved with `search_stops`. |

Returns an array sorted lexicographically by `route_id`. Each item contains
`route_id`, `agency_id`, `route_short_name`, and `route_long_name` as strings,
and `route_type` as an integer. Unlike `search_routes`, it does not return
`directions` or `score`.

Input:

```json
{"stop_id": "S3"}
```

Output:

```json
[
  {
    "route_id": "R2",
    "agency_id": "1",
    "route_short_name": "2",
    "route_long_name": "Śrem",
    "route_type": 3
  }
]
```

A known stop with no routes returns `[]`. An unknown stop produces a tool error:
`Unknown stop_id. Use search_stops to find a valid stop.`

## `get_departures`

Return scheduled departures for a specified stop, line, and GTFS service day.

| Input | JSON type | Required | Meaning |
|---|---|---|---|
| `stop_id` | string | yes | Exact stop ID resolved with `search_stops`. |
| `route_id` | string | yes | Exact route ID resolved with `search_routes`. |
| `date` | string | yes | Valid service date in `YYYY-MM-DD` format. |
| `time` | string | yes | Inclusive lower bound, `HH:MM`, from `00:00` to `23:59`. |
| `direction_id` | integer or null | no | `0` or `1`; omitted or `null` means both directions. |
| `limit` | integer | no | Number of results, from `1` to `100`; default `5`. |

The response object contains:

| Field | JSON type | Meaning |
|---|---|---|
| `stop_id`, `stop_name` | string | Selected stop ID and display name. |
| `route_id`, `route_short_name` | string | Selected route ID and displayed line. |
| `date`, `time` | string | Requested service date and time. |
| `direction_id` | integer or null | Requested direction filter. |
| `data_source` | string | Always `"static_mock"` in the current implementation. |
| `realtime` | boolean | Always `false`; these are scheduled times. |
| `departures` | array | Matching departures ordered by scheduled departure time. |

Each departure contains:

| Field | JSON type | Meaning |
|---|---|---|
| `departure_time` | string | GTFS `HH:MM:SS`; hours may exceed `23`. |
| `departure_datetime` | string | ISO calendar datetime without a timezone offset. |
| `trip_id`, `route_id`, `route_short_name` | string | Trip and route identifiers and displayed line. |
| `trip_headsign` | string | Trip destination/headsign. |
| `direction_id` | integer | Trip direction. |
| `stop_sequence` | integer | Position in the trip; not a stop identifier. |

Input:

```json
{"stop_id": "S2", "route_id": "R2", "date": "2026-06-15", "time": "06:10"}
```

Output:

```json
{
  "stop_id": "S2",
  "stop_name": "Żabinko",
  "route_id": "R2",
  "route_short_name": "2",
  "date": "2026-06-15",
  "time": "06:10",
  "direction_id": null,
  "data_source": "static_mock",
  "realtime": false,
  "departures": [
    {
      "departure_time": "06:10:00",
      "departure_datetime": "2026-06-15T06:10:00",
      "trip_id": "T3",
      "route_id": "R2",
      "route_short_name": "2",
      "trip_headsign": "Dworzec",
      "direction_id": 1,
      "stop_sequence": 0
    }
  ]
}
```

### Service dates and no results

`date` selects a GTFS service day, not a rolling 24-hour window. For service day
`2026-06-15`, `25:30:00` corresponds to `2026-06-16T01:30:00`. It can appear in
that service day's results even though input `time` cannot exceed `23:59`.
Returned datetimes carry no timezone offset; do not interpret them as UTC.
The tool searches only the requested service day and respects weekly service
calendars and date exceptions. It does not automatically include overnight trips
from the previous service day or advance to the next service day.

An empty `departures` array means no departures match all filters on that service
day at or after the requested time. This includes an existing route that does
not serve the selected stop, an inactive date, or no service in the selected
direction. It does not mean the stop or route is unknown. The response still
contains all metadata fields. No delay or predicted-time fields are provided;
do not infer zero delay or live vehicle status from a static timetable.

### Errors

The tool raises `ToolError` for the following invalid arguments. These are MCP
tool failures, not successful response objects with an `error` field.

| Condition | Error message |
|---|---|
| Wrong date format | `Invalid date. Use YYYY-MM-DD.` |
| Impossible calendar date | `Invalid date. Provide a valid calendar date as YYYY-MM-DD.` |
| Invalid time | `Invalid time. Use HH:MM from 00:00 through 23:59.` |
| Direction other than `0`, `1`, or null | `Invalid direction_id. Use 0 or 1, or omit it.` |
| Limit outside `1`–`100` | `Invalid limit. Use a number from 1 to 100.` |
| Unknown stop | `Unknown stop_id. Use search_stops to find a valid stop.` |
| Unknown route | `Unknown route_id. Use search_routes to find a valid line.` |

Missing required arguments and incompatible input types are handled by MCP
framework validation; their exact error text is not specified here.

## `echo`

Diagnostic tool. Required input `text` is a string; the result is the same string,
including an empty string or Polish characters.

Input:

```json
{"text": "Żabinko"}
```

Output:

```json
"Żabinko"
```
