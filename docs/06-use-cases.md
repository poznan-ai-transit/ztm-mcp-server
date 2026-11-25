# Use cases from project sessions

These scenarios were exercised in this project's Codex sessions on 18 November
2026 and reviewed on 25 November 2026. They describe user goals and the current
tool boundary, rather than promising that historical journeys or venue hours
remain valid. Re-run searches for the requested date within the loaded timetable.

Capability status below reflects `origin/main` at `da7ce68`, checked on
25 November 2026. Real-time departure enrichment was merged in
[PR #72](https://github.com/poznan-ai-transit/ztm-mcp-server/pull/72).
The session examples predate this check; the real-time scenario below is an
additional use case enabled by main, not a claim about those session results.
This use-case branch has not incorporated main's runtime changes.

## Stop discovery and conversational follow-ups

**Prompt:** “Find stops called Rondo Kaponiera in Poznań.” The Polish session
began with “Znajdz przystanek Rondo Kaponiera”.

Use `search_stops` to resolve the name. Present the matching platforms with
public stop codes; retain their string `stop_id` values for subsequent calls.
Same-name platforms form one stop complex, so do not ask the user to choose an
internal ID. Clarify only genuinely different places. In the English session,
the search returned ten platforms; that count is an observation, not a fixed rule.

For “Which lines serve those stops?”, call `get_routes_for_stop` for each
matching platform and deduplicate routes. This directory lookup needs no date
or time and does not establish that a route runs on a particular day.

## Scheduled departures and no-service answers

**Prompt:** “Show the next five scheduled departures of line 15 from Rondo
Kaponiera today after 14:00.”

Resolve the line with `search_routes`, query `get_departures` across applicable
platforms, then merge, deduplicate and order results before applying the overall
limit. Show departure time, headsign and boarding platform. Preserve context
for “What's the next one?” and clarify only missing required information.

The same session also tried line 221 at Rondo Kaponiera and found no matching
departures. Report an empty result honestly; use route lookup to explain the
mismatch when supported. Do not substitute a different line without saying so.

## Departure delays, predictions and cancellations

**Prompt:** “Is my next line 15 tram from Rondo Kaponiera delayed or cancelled?”

Resolve the stop and route for the requested date, then call `get_departures`
across applicable platforms. On current main, the response retains scheduled
times and adds available GTFS-RT information: `departure_delay` in seconds,
`realtime_departure_datetime`, `realtime_status` and `cancelled`. Show scheduled
and predicted times separately and identify cancellations or skipped stops
when reported. Do not recommend boarding a cancelled trip or at a skipped stop.

The server refreshes trip updates every 30 seconds. Inspect each departure:
the top-level `realtime` flag means at least one returned departure has a
matching trip update, not that every departure has a prediction. A departure's
`realtime=true` can also have no stop-level prediction. Missing updates or null
delay fields mean unknown, not on time. Refresh failures retain the previous
snapshot, so describe values as the latest available feed information rather
than guaranteed current vehicle status. Historical timetable queries do not
provide a real-time archive.

This capability covers trip updates, not live vehicle locations or general
service alerts. Alerts have an implementation on `origin/81-service-alerts`
but are not merged into the checked main revision; vehicle tracking is not
exposed through MCP tools.

## Journeys with a transfer preference

**Prompt:** “Find a journey from Poznań Główny to Rondo Kaponiera today after
14:00, with at most one transfer.” A Polish follow-up requested the reverse trip.

Resolve both stop names and consider applicable origin/destination platform
combinations. Call `search_connection` with `max_transfers=1` (or `0` for
direct-only travel), merge duplicate journeys and compare returned alternatives.
Show departure and arrival, lines, headsigns, boarding/alighting platforms,
duration and changes. Direct journeys satisfy an “at most one” preference.

The search uses static GTFS and transfers at the same `stop_id`; it does not
include walking transfers, live delays or a guaranteed connection buffer.
An empty result means no journey was found under the search constraints, not
proof that no possible journey exists.

## Activities between fixed commitments

**Prompt:** “I finish classes at Politechnika at 11:00 today and need to be at
Krauthofera by 12:30. Find something to do, including tram time.”

The assistant combines `search_stops` and `search_connection` with independent
research about cafés, lunch or walks. Compare feasible activity stops against
the onward journey deadline. Include walking estimates, time at the activity,
when to leave and an explicit arrival buffer. Ask for an address if a street or
campus location materially affects feasibility.

The session explored both travelling directly toward Krauthofera and visiting
an Old Town café en route. The MCP supplies the scheduled transit legs; the
assistant supplies the activity plan. There is no arrive-by or activity-planning
tool: compare returned arrival times against the deadline, and do not claim
exhaustive optimization.

For near-term travel, the assistant can additionally check `get_departures`
for each selected leg and report available updates. This does not recalculate
the journey or guarantee that its transfers or deadline remain feasible.

## Meeting a friend at a café

**Prompt:** “I'm near Rondo Kaponiera and my friend is at Politechnika. Find
a café we can both reach around 17:00 today, preferably without changing vehicles.”

Establish the earliest departure time for each person, research candidate
venues and their opening hours, and resolve nearby transit stops. Search each
origin to candidate stops with `search_connection`; try `max_transfers=0` for
the direct preference. Compare arrivals and waiting time among the returned
options, adding each person's final walk to the café.

Show one itinerary per person and the proposed meeting time. The observed
session used Aleje Marcinkowskiego as a shared arrival stop before walking to
a café. Matching stop names may still mean different platforms. Venue hours,
walking times and the meeting recommendation are external research or assistant
estimates, not MCP results. There is no dedicated multi-origin optimizer.

Departure updates can inform each person's immediate boarding plan, but the
meeting recommendation and arrival comparison remain based on scheduled
journeys; predicted arrival times are not supplied by `search_connection`.

## Common expectations

- Use Europe/Warsaw local time and resolve relative dates when the prompt runs.
- Reuse conversation context; ask concise questions for missing required details.
- Distinguish scheduled journeys from feed-backed departure predictions; never infer zero delay from a missing update.
- Report real-time information per departure, including cancellations and skipped stops; do not claim live vehicle tracking.
- Obtain stop and route facts from tools; never invent IDs, journeys or departures.
- Keep venue facts sourced separately and label unverified hours or walking estimates.
- Explain dataset date limits and unsupported requests clearly.
