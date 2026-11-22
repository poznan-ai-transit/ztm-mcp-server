# Multi-step agent evaluation: results

## Summary

We tested whether an LLM agent can answer a timetable question that needs **several MCP tools
in a row**, for example: find the stop, find the line, then fetch departures.

| Model | Passed | |
|---|---|---|
| Claude Haiku 5.5 | 24 / 24 (100%) | Reliable. Chains tools correctly and asks when information is missing. |
| Gemini 2.5 Flash Lite | 19 / 24 (79%) | Good at chains, but answers "not found" without searching and invents dates. |
| GPT-4o-mini (the demo model) | 17 / 24 (71%) | Often skips lookups, uses a platform code as `stop_id`, invents dates. |

**Main takeaway:** the tools and their descriptions are good enough for a capable model
(Haiku). Weaker models fail mostly by *taking shortcuts*: they skip a lookup, reuse data from
the question as an ID, or invent a date instead of asking. They do not fail because the tool
chain is unclear.

The demo (`src/demo.py`) uses GPT-4o-mini. It handles most questions, but it is the weakest
of the three models here, especially when the date is missing (0 / 3).

## What was tested

Each scenario gives the agent **one question**. The agent decides which tools to call and
in what order, and the results of one call feed the next. Assertions check the whole
sequence of calls, not only the final answer.

| # | Question sent to the model | Expected behaviour |
|---|---|---|
| M1 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00. | `search_stops` → `search_routes` → `get_departures` |
| M2 | Which lines serve stop Rondo Kaponiera, platform RKAP72? Then show departures of line 15 on 2026-11-24 at 12:00. | `search_stops` → `get_routes_for_stop` → `get_departures` |
| M3 | I am at Rondo Kaponiera, platform RKAP71, and want to get to Bułgarska Enea Stadion by line 15. When does it leave on 2026-11-24 at 12:00? | Full chain, using the correct platform |
| M4 | Show departures of line 15 from stop Plac Nieistniejacy Xyz on 2026-11-24 at 12:00. | Search, then say "not found". No departures call. |
| M5 | Compare departures of lines 15 and 11 from stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00. | Two `get_departures` calls, one per line |
| M6 | Does line 15 exist, and if so when does it leave stop Rondo Kaponiera, platform RKAP72, on 2026-11-24 at 12:00? | Check the line, then fetch departures |
| M8 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72. | Ask for the date and time. No departures call. |
| M9 | Show departures of line 15 from stop Rondo Kaponiera, platform RKAP72, on 2026-12-24 at 12:00. | The date is outside the timetable: report the error, invent nothing |

There is no M7: see [Same-name platforms](#same-name-platforms-and-m7).

Every scenario also checks two things:

- **IDs come from tools.** Every `stop_id` and `route_id` passed to `get_departures` must
  appear in an earlier search result.
- **No invented times.** Every time in the answer must appear in a tool result or in the
  question.

Setup: run on 2026-11-22 against the real MCP server with the real GTFS data. Scenarios ask
about 2026-11-24 at 12:00. 3 models, 3 repeats each, 72 runs in total. To repeat the run,
use `npm run eval:multi:repeat` in `evals/` (needs the MCP server and `OPENROUTER_API_KEY`).

### What the model received

Each run starts a fresh conversation with two messages: the system prompt below and the
question from the table above as the user message. The model also gets the tool definitions
(names, descriptions, parameters) exactly as the MCP server publishes them; they are
described in [`04-mcp-tool-contract.md`](04-mcp-tool-contract.md). The agent may call tools
for up to 8 rounds before it has to answer.

System prompt (the same one is used in the single-turn evals; line breaks added here for
readability):

```text
You are an assistant for public transport in Poznan (ZTM Poznan) ONLY. Rules:
1) For stop-existence questions, use search_stops. For route-existence questions, use
   search_routes. Never answer these from memory.
2) For departure questions, use get_departures. If the tool requires a stop identifier,
   first resolve the stop with search_stops, then call get_departures with the returned
   identifier; do not stop after resolving the stop.
3) If the city is not stated, assume Poznan.
4) If the question concerns a place outside Poznan, or is not about Poznan public transport,
   politely decline and do NOT call any tool.
5) If required information is missing, ask for the missing information and do NOT call a
   tool that cannot be used without it. A clarification may be phrased as a request and does
   not need to end with a question mark.
6) If a tool returns no results, say so; never invent stops, routes or times.
7) Never invent departure times; only report times returned by get_departures.
Reply in the language the user writes in.
```

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
| M8 Missing date and time | 3 | 2 | **0** |
| M9 Date out of range | 3 | 3 | 3 |

\* One Gemini run failed on an OpenRouter error (502, empty response from Google), not on the
model's behaviour.

## What went wrong

### GPT-4o-mini

- **Invents a date (M8, 3 of 3).** With no date in the question it calls `get_departures`
  with a made-up date (2023-10-04, 2023-10-06, 2023-10-07), gets a
  "date not supported" error, and only then asks the user, or just reports the error.
- **Skips the line lookup (M1, 2 of 3).** It passes "15" from the question straight to
  `get_departures` without `search_routes`. This works today only because the route ID
  happens to equal the line number.
- **Uses the platform code as `stop_id` (M2, M6).** It calls tools with `stop_id: "RKAP72"`
  (the `stop_code`), gets an error, and then searches the stop. In M6 it recovers and gives
  the right answer. In M2 it then picks the wrong platform (RKAP74 instead of RKAP72) and
  lists lines for the wrong stop.

### Gemini 2.5 Flash Lite

- **Answers without looking (M4, 3 of 3).** It says "I can't find this stop" without calling
  `search_stops`. The answer happens to be right, but it is not based on data.
- **Invents a date (M8, 1 of 3)**, like GPT-4o-mini (2024-05-15).

### Claude Haiku 5.5

No failures in this run.

## Recommendations

1. **Consider a stronger model for the demo.** Haiku passed every run at a similar price to
   GPT-4o-mini. If the demo stays on GPT-4o-mini, the prompt changes below matter most.
2. **Strengthen the system prompt.** Add "never assume a date; ask for it" (M8, both weaker
   models) and "always look up the line with search_routes before get_departures" (M1).
3. **Make `search_stops` match platform codes.** Users name platforms by code (RKAP72), but a
   query like "RKAP72" currently finds a different stop, so the model has to search by name
   and filter the results itself. This is a known issue and a fix is planned.

## Same-name platforms and M7

Each platform is a separate stop in the data (Rondo Kaponiera has 10). The team has decided
that platforms with the same name should behave as **one stop**: the agent should not ask
which platform, but use all of them. The target solution is stop groups on the server side
(issues #60-#62).

Until stop groups exist, every scenario names the platform by its code (e.g. RKAP72), and
scenario M7, which asked about a stop by name only, is removed. It will come back, with the
new expected behaviour, once stop groups are implemented.

## Limitations

- **Server version.** This run used the server from the `agent-testing` branch. `main` has
  newer tool descriptions (e.g. "Same-name results are platforms, not ambiguity") and new
  tools. The evals should be repeated after merging `main`.
- **Server instructions are not sent.** The eval sends the tool definitions but not the
  server's `instructions`, which a real MCP client would show to the model.
- **The timetable window moves.** The server only serves about two weeks of data
  (2026-11-22 to 2026-12-05 at the time of the run). Scenario dates must be updated when the
  window moves, or every scenario fails with a date error.
- **3 repeats per model.** That is enough to see clear patterns but not to compare models
  that are close to each other.
- **The grader is one of the tested models.** M4, M8 and M9 partly use a model-graded check
  (`llm-rubric`) run by Claude Haiku, so these scenarios may slightly favour Haiku. The
  tool-sequence checks are deterministic.
- **The "no invented times" check is strict.** It cannot tell a departure time from a time
  window in a summary ("between 12:00 and 13:00"). In an earlier run this flagged one correct
  Haiku answer. We accept that to keep invented times from slipping through.

## Earlier run: Llama 3.1 8B

An earlier run included Llama 3.1 8B instead of GPT-4o-mini. It passed 1 of 27 runs: it
called tools with placeholder IDs such as `"<stop_id>"` and looped until the step limit.
Because it failed almost everything, it showed nothing about the tools, so it was replaced
by GPT-4o-mini, the model used in the demo.
