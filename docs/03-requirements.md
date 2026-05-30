# Document 3: Functional and Non-Functional Requirements

## Purpose
This document defines baseline requirements for the MVP of `ztm-mcp-server`.
It is consistent with `docs/01-product-goal.md` and `docs/02-mvp.md`.

## Scope (MVP)
- Supported city: **Poznan only**.
- Supported data: **static timetable data only** (mocked/hardcoded in MVP).
- Supported interaction: user asks transit questions in natural language, agent calls MCP tools.

## Assumptions
- The first implementation phase uses mock tools and deterministic hardcoded datasets, structured to allow a swift transition to the live API in subsequent sprints.
- Any model/agent may be used, but tool contracts must stay model-agnostic.
- Real-time feeds, routing, and external map integration are outside MVP.

## Functional Requirements (FR)

### FR-1: City Boundary Enforcement (Poznan-only)
The system must handle transit queries for Poznan and reject queries for other cities.

**Acceptance criteria**
- For out-of-scope cities (e.g., Krakow), the agent returns a clear refusal message.
- The refusal message includes what is supported (Poznan timetable help).

### FR-2: Capability Awareness
The agent must explain its current capabilities and limits when needed.

**Acceptance criteria**
- On questions like "What can you do?", the agent states MVP scope.
- The response explicitly says that real-time tracking/delays are not yet supported in MVP.

### FR-3: Static Timetable Query (Mock Tools)
The system must answer timetable questions for a specific line at a specific stop using mock data.

**Acceptance criteria**
- Given complete required inputs, the agent returns at least one relevant departure time.
- Response references the interpreted context (line, stop, direction if provided).

### FR-4: Missing Context Clarification
Before calling a tool, the agent must ask follow-up questions for missing required input.

**Acceptance criteria**
- If destination is given without start location, the agent asks for start location.
- If line/stop is missing, the agent asks for the missing field(s) before tool call.

### FR-5: Deterministic Mock Tool Contract
Mock tools must expose stable input/output schema to enable testing with different agents.

**Acceptance criteria**
- Same input returns the same output in repeated runs.
- Tool output includes enough fields for rendering a user answer (at least line, stop, departures).

### FR-6: Directory Lookup (List Routes and Stops)
The agent must be able to return baseline information about available resources (lines and stops) to guide the user's queries.

**Acceptance criteria**
- When a user asks for a list of available lines or stops, the agent queries the mock server directory.
- The agent successfully returns a structured summary of supported routes and stops in Poznań.

### FR-7: Static GTFS Data Refresh (System-level)
The system must support an automated background mechanism to refresh the local static dataset from an external source to maintain data relevance.

**Acceptance criteria**
- The system executes an automated daily task (e.g., at 06:00) to download the latest static GTFS snapshot from the ZTM Open Data API.
- The downloaded data seamlessly replaces the existing local static dataset without causing downtime for the mock tools.

## Non-Functional Requirements (NFR)

### NFR-1: Response Time (MVP)
The system should respond quickly for typical mock-based requests.

**Acceptance criteria**
- Typical query-response cycle completes within 2 seconds in local development setup.

### NFR-2: Reliability and Determinism
MVP behavior should be predictable for repeatable testing.

**Acceptance criteria**
- Repeated tests with identical inputs produce identical tool outputs.
- Failures are handled gracefully without crashing the session.

### NFR-3: Error Message Quality
Errors and refusals must be actionable and understandable.

**Acceptance criteria**
- Error/refusal message explains why the request failed.
- Message includes next step (e.g., provide missing stop/city, ask about Poznan).

### NFR-4: Usability of Responses
Responses should be short, clear, and commuter-friendly.

**Acceptance criteria**
- The answer avoids internal protocol details unless explicitly requested.
- Follow-up questions are concise and ask only for missing fields.

### NFR-5: Observability for Development
The MVP should allow basic debugging of user query and tool call flow.

**Acceptance criteria**
- Request/response traces can be inspected in local logs during development.
- Validation errors are visible with clear field-level information.

## Out of Scope for MVP
- Real-time delays and "has the bus already left" logic from GTFS-RT.
- Live vehicle position tracking.
- Route planning/path-finding algorithms.
- Google Maps integration.
- Accessibility extensions beyond basic mock fields.
- Specific transit features like low-floor vehicle identification (planned for Phase 2).
