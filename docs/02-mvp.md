# Document 2: Minimum Viable Product (MVP)

## Scope Boundaries
*   **Supported Location:** Strictly Poznań. The agent must be explicitly configured to refuse any transit queries outside of Poznań (e.g., Kraków).
*   **Capability Awareness:** The agent must be able to clearly communicate its purpose and limitations to the user (e.g., "I can help you check static public transport schedules in Poznań").
*   **Supported Data:** Static timetables only. Real-time data (delays, live vehicle tracking) is out of scope for the MVP.

## MVP Implementation (Mock Phase)
*   **Mock Server:** Deploy a basic FastMCP server.
*   **Hardcoded Tools:** Implement mock tools returning hardcoded, static timetable data to validate the architecture and test the agent's connection.
*   **Static Schedule Queries:** Provide answers regarding specific timetables for a specific bus at a specific stop based on the mock data.
*   **Context Gathering:** Ensure the agent correctly prompts the user for missing information before calling a tool (e.g., asking "What is your starting location?" if the user only provides a destination).
