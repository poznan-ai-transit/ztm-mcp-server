# Multi-step agent evaluation: results

## Summary

We tested whether an LLM agent can answer a timetable question that needs **several MCP tools
in a row**, for example: find the stop, find the line, then fetch departures.

| Model | Passed | Notes |
|---|---|---|
| Claude Haiku 5.5 | 24 / 24 (100%) | No failures. |
| Gemini 2.5 Flash Lite | 19 / 24 (79%) | Answers "not found" without searching, sometimes invents a date. |
| GPT-4o-mini (demo model) | 17 / 24 (71%) | Skips lookups, uses a platform code as `stop_id`, invents dates. |

The tools are clear enough for a capable model. Weaker models fail by taking shortcuts: they
skip a lookup, reuse text from the question as an ID, or invent a date instead of asking.

## What was tested

Each scenario gives the agent **one question**; the agent decides which tools to call and in
what order. Assertions check the whole call sequence, not only the final answer.

| # | Question sent to the model | Expected behaviour |
|---|---|---|
| M1 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00. | `search_stops` → `search_routes` → `get_departures` |
| M2 | Which lines serve stop Rondo Kaponiera, platform RKAP72? Then show departures of line 15 on 2026-11-24 at 12:00. | `search_stops` → `get_routes_for_stop` → `get_departures` |
| M3 | I am at Rondo Kaponiera, platform RKAP71, and want to get to Bułgarska Enea Stadion by line 15. When does it leave on 2026-11-24 at 12:00? | Full chain, using the correct platform |
| M4 | Show departures of line 15 from stop Plac Nieistniejacy Xyz on 2026-11-24 at 12:00. | Search, then say "not found". No departures call. |
| M5 | Compare departures of lines 15 and 11 from stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00. | Two `get_departures` calls, one per line |
| M6 | Does line 15 exist, and if so when does it leave stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00? | Check the line, then fetch departures |
| M7 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72. | Ask for the date and time. No departures call. |
| M8 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72, on 2026-12-24 at 12:00. | Report that the date is outside the timetable, invent nothing |

Every scenario also checks that each `stop_id` and `route_id` passed to `get_departures` comes
from an earlier search result, and that every time in the answer appears in a tool result or
in the question.

Setup: run on 2026-11-22 against the real MCP server and GTFS data; 3 models × 3 repeats,
72 runs. To repeat it, run `npm run eval:multi:repeat` in `evals/` (needs the MCP server and
`OPENROUTER_API_KEY`).

## Results per scenario

Passed runs out of 3.

| Scenario | Haiku | Gemini | GPT-4o-mini |
|---|---|---|---|
| M1 Stop → line → departures | 3 | 2 * | **1** |
| M2 Lines at a stop → departures | 3 | 3 | 2 |
| M3 To the stadium | 3 | 3 | 3 |
| M4 Nonexistent stop | 3 | **0** | 3 |
| M5 Two lines at one stop | 3 | 3 | 3 |
| M6 Line exists → departures | 3 | 3 | 2 |
| M7 Missing date and time | 3 | 2 | **0** |
| M8 Date out of range | 3 | 3 | 3 |

\* One run failed on an OpenRouter error (502), not on the model's behaviour.

## What went wrong

**GPT-4o-mini**

- **Invents a date (M7, 3 of 3).** It calls `get_departures` with a made-up date
  (e.g. 2023-10-06), gets an error, and only then asks the user or reports the error.
- **Skips the line lookup (M1, 2 of 3).** It passes "15" straight to `get_departures`. This
  works only because the route ID happens to equal the line number.
- **Uses the platform code as `stop_id` (M2, M6).** It passes "RKAP72", gets an error and then
  searches the stop. In M2 it then picks the wrong platform (RKAP74).

**Gemini 2.5 Flash Lite**

- **Answers without looking (M4, 3 of 3).** It says the stop does not exist without calling
  `search_stops`.
- **Invents a date (M7, 1 of 3)**, like GPT-4o-mini.
