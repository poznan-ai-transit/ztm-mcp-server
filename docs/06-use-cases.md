# Use cases from project sessions

These scenarios were exercised in this project's Codex sessions on 18 November
2026 and reviewed on 25 November 2026. They describe user goals and the current
tool boundary, rather than promising that historical journeys or venue hours
remain valid. Re-run searches for the requested date within the loaded timetable.

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

## Common expectations

- Use Europe/Warsaw local time and resolve relative dates when the prompt runs.
- Reuse conversation context; ask concise questions for missing required details.
- Label results as scheduled and exclude claims about live delays or positions.
- Obtain stop and route facts from tools; never invent IDs, journeys or departures.
- Keep venue facts sourced separately and label unverified hours or walking estimates.
- Explain dataset date limits and unsupported requests clearly.

## Session sources

- [Find Rondo Kaponiera stops](codex://threads/01a1edaf-02e1-7363-92a0-cd999e242adf): platforms, departures, no-service answer and journeys.
- [Znajdź przystanek Rondo Kaponiera](codex://threads/01a1edba-9765-7ae0-bb0e-da437a3c182c): Polish stop lookup and direct journey follow-up.
- [Plan Poznań free hour](codex://threads/01a1edc1-2b6a-79f1-a22b-cbb00022369c): activities between classes and an appointment.
- [Find a café near transit](codex://threads/01a1edc9-fae5-7032-854b-f3501ef051cd): two-origin meeting plan.
- [Run MCP server locally](codex://threads/01a1edac-0f09-7d82-9ec5-822319aafd62): demo prompts, integration ideas and static-data limitations.

Session links open in Codex. The descriptions above are self-contained for
readers without access to those sessions.
