"""Tests for the MCP tool and resource callables.

FastMCP's ``@mcp.tool()`` / ``@mcp.resource()`` register the function as a side
effect but return the original callable, so they can be invoked directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server.resources import list_routes_and_stops
from mcp_server.tools import (
    echo,
    get_departures,
    get_routes_for_stop,
    search_routes,
    search_stops,
)
from server import create_server
from services.ztm_service import ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule

# --------------------------------------------------------------------------- #
# echo tool
# --------------------------------------------------------------------------- #


def test_echo_returns_input():
    assert echo("hello") == "hello"


def test_echo_preserves_polish_diacritics():
    assert echo("Świętego Marcina") == "Świętego Marcina"


def test_echo_empty_string():
    assert echo("") == ""


def test_search_stops_uses_schedule_wrapper(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    results = search_stops("zabink", ctx=_ctx_with(ZTMStaticSchedule.instance()))
    assert results
    assert results[0]["stop_id"] == "S2"
    assert results[0]["stop_name"] == "Żabinko"


def test_search_routes_uses_schedule_wrapper(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    results = search_routes("Łęczyca", ctx=_ctx_with(ZTMStaticSchedule.instance()))
    assert results
    assert results[0]["route_id"] == "R1"
    assert results[0]["route_long_name"] == "Łęczyca"
    assert "directions" in results[0]


@pytest.mark.parametrize("search", [search_stops, search_routes])
@pytest.mark.parametrize("query", ["", " ", "\t\n"])
def test_search_rejects_blank_queries(search, query):
    with pytest.raises(ToolError, match="Invalid query"):
        search(query, ctx=_ctx_with(None))


@pytest.mark.parametrize("search", [search_stops, search_routes])
@pytest.mark.parametrize("limit", [-1, 0, 101, True, 1.5])
def test_search_rejects_invalid_limits(search, limit):
    with pytest.raises(ToolError, match="Invalid limit. Use an integer from 1 to 100."):
        search("Rynek", limit=limit, ctx=_ctx_with(None))


@pytest.mark.parametrize("search,query", [(search_stops, "Rynek"), (search_routes, "R1")])
@pytest.mark.parametrize("limit", [1, 100])
def test_search_accepts_limit_boundaries(sample_gtfs, search, query, limit):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    result = search(query, limit=limit, ctx=_ctx_with(schedule))
    assert 1 <= len(result) <= limit
    assert search("zzzzzzzzzzzzzzzzzzzzzz", limit=limit, ctx=_ctx_with(schedule)) == []


def test_departures_filter_line_before_limit(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures(
        "S2", "R2", "2026-06-15", "05:00", limit=1, ctx=_ctx_with(ZTMStaticSchedule.instance())
    )
    assert result["stop_name"] == "Żabinko"
    assert result["route_short_name"] == "2"
    assert result["realtime"] is False
    assert [d["departure_time"] for d in result["departures"]] == ["06:10:00"]
    assert result["departures"][0]["trip_headsign"] == "Dworzec"


def test_departures_filter_direction_before_limit(sample_gtfs):
    sample_gtfs["trips"][1]["direction_id"] = "1"
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures(
        "S1",
        "R1",
        "2026-06-15",
        "05:00",
        direction_id=1,
        limit=1,
        ctx=_ctx_with(ZTMStaticSchedule.instance()),
    )
    assert [d["trip_id"] for d in result["departures"]] == ["T2"]
    assert result["departures"][0]["departure_datetime"] == "2026-06-16T01:30:00"


@pytest.mark.parametrize(
    "stop,route,day,time,direction",
    [
        ("S3", "R1", "2026-06-15", "05:00", None),
        ("S2", "R2", "2026-06-15", "06:11", None),
        ("S2", "R2", "2026-06-14", "05:00", None),
        ("S2", "R2", "2026-06-15", "05:00", 0),
    ],
)
def test_departures_no_matching_service(sample_gtfs, stop, route, day, time, direction):
    ZTMStaticSchedule.load(sample_gtfs)
    assert (
        get_departures(
            stop, route, day, time, direction, ctx=_ctx_with(ZTMStaticSchedule.instance())
        )["departures"]
        == []
    )


@pytest.mark.parametrize("day", ["2026-05-31", "2026-07-01"])
def test_departures_reject_dates_outside_dataset(sample_gtfs, day):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    with pytest.raises(ToolError, match="Supported date range: 2026-06-01 to 2026-06-30") as exc:
        get_departures("S1", "R1", day, "05:00", ctx=_ctx_with(schedule))
    assert day in str(exc.value)


@pytest.mark.parametrize(
    "day,departure_datetime",
    [
        ("2026-06-01", "2026-06-02T01:30:00"),
        ("2026-06-30", "2026-07-01T01:30:00"),
    ],
)
def test_departures_accept_feed_boundaries_and_overnight_trips(
    sample_gtfs, day, departure_datetime
):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures("S1", "R1", day, "23:00", ctx=_ctx_with(schedule))
    assert result["departures"][0]["departure_datetime"] == departure_datetime


def test_departures_calendar_exceptions_and_inclusive_time(sample_gtfs):
    sample_gtfs["calendar_dates"] = [
        {"service_id": "WKD", "date": "20260614", "exception_type": "1"},
        {"service_id": "WKD", "date": "20260615", "exception_type": "2"},
    ]
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures(
        "S2", "R2", "2026-06-14", "06:10", ctx=_ctx_with(ZTMStaticSchedule.instance())
    )
    assert result == get_departures(
        "S2", "R2", "2026-06-14", "06:10", ctx=_ctx_with(ZTMStaticSchedule.instance())
    )
    assert result["departures"][0]["departure_time"] == "06:10:00"
    assert (
        get_departures(
            "S2", "R2", "2026-06-15", "06:10", ctx=_ctx_with(ZTMStaticSchedule.instance())
        )["departures"]
        == []
    )


@pytest.mark.parametrize(
    "override",
    [
        {"date": "2026-02-30"},
        {"date": "20260615"},
        {"time": "24:00"},
        {"time": "06:60"},
        {"time": "6:10"},
        {"limit": 0},
        {"limit": -1},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.5},
        {"direction_id": 2},
        {"stop_id": "missing"},
        {"route_id": "missing"},
    ],
)
def test_departures_invalid_input(sample_gtfs, override):
    ZTMStaticSchedule.load(sample_gtfs)
    arguments = {
        "stop_id": "S2",
        "route_id": "R2",
        "date": "2026-06-15",
        "time": "06:10",
    }
    with pytest.raises(ToolError):
        get_departures(**(arguments | override), ctx=_ctx_with(ZTMStaticSchedule.instance()))


def test_routes_for_stop_are_sorted_and_serializable(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_routes_for_stop("S2", ctx=_ctx_with(ZTMStaticSchedule.instance()))
    assert [r["route_id"] for r in result] == ["R1", "R2"]
    assert result[0]["route_short_name"] == "1"
    assert result == get_routes_for_stop("S2", ctx=_ctx_with(ZTMStaticSchedule.instance()))
    with pytest.raises(ToolError, match="search_stops"):
        get_routes_for_stop("missing", ctx=_ctx_with(ZTMStaticSchedule.instance()))


def _resolve_schema(root: dict, node: dict) -> dict:
    """Follow local JSON references while also accepting inline schemas."""
    seen = set()
    while "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/") and ref not in seen, ref
        seen.add(ref)
        node = root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def test_new_tools_through_mcp(sample_gtfs, monkeypatch):
    ZTMStaticSchedule.load(sample_gtfs)

    monkeypatch.setattr(ZTMService, "start_daily_refresh", lambda self: None)
    monkeypatch.setattr(ZTMService, "stop_daily_refresh", lambda self: None)

    async def check():
        async with Client(create_server()) as client:
            # The lifespan has captured the schedule; tools must not fetch the singleton.
            def unexpected_instance():
                raise AssertionError("Tool bypassed lifespan storage")

            monkeypatch.setattr(ZTMStaticSchedule, "instance", unexpected_instance)
            tools = await client.list_tools()
            assert all("ctx" not in tool.inputSchema["properties"] for tool in tools)
            by_name = {tool.name: tool for tool in tools}
            for name in ("get_routes_for_stop", "get_departures"):
                stop_id_schema = by_name[name].inputSchema["properties"]["stop_id"]
                assert stop_id_schema["type"] == "string"
                assert "stop_id" in by_name[name].inputSchema["required"]
                assert "search_stops" in stop_id_schema["description"]
                assert "leading zeros" in stop_id_schema["description"]
                assert "stop_sequence" in stop_id_schema["description"]
            for name, fields in (
                ("search_stops", {"stop_id", "stop_code", "score"}),
                ("search_routes", {"route_id", "directions", "score"}),
                ("get_routes_for_stop", {"route_id", "agency_id", "route_type"}),
            ):
                schema = by_name[name].outputSchema
                assert schema is not None
                result = _resolve_schema(schema, schema)
                # FastMCP wraps list outputs in a result property for MCP's
                # object output schema; model fields may be inline or referenced.
                if result.get("type") == "object":
                    assert "result" in result["required"]
                    result = _resolve_schema(schema, result["properties"]["result"])
                assert result["type"] == "array"
                item = _resolve_schema(schema, result["items"])
                assert fields <= item["properties"].keys()
                assert fields <= set(item["required"])
            schema = by_name["get_departures"].outputSchema
            assert schema is not None
            response = _resolve_schema(schema, schema)
            departures_schema = _resolve_schema(schema, response["properties"]["departures"])
            assert departures_schema["type"] == "array"
            departure = _resolve_schema(schema, departures_schema["items"])
            assert {"departure_time", "departure_datetime", "stop_sequence"} <= (
                departure["properties"].keys()
            )
            names = {tool.name for tool in tools}
            stops = await client.call_tool("search_stops", {"query": "Rynek"})
            assert stops.structured_content["result"][0]["stop_id"] == "S1"
            found_routes = await client.call_tool("search_routes", {"query": "R1"})
            assert found_routes.structured_content["result"][0]["route_id"] == "R1"
            assert {"get_departures", "get_routes_for_stop"} <= names
            departure_arguments = {
                "stop_id": "S2",
                "route_id": "R2",
                "date": "2026-06-15",
                "time": "06:10",
            }
            departures = await client.call_tool("get_departures", departure_arguments)
            assert departures.structured_content["departures"][0]["departure_time"] == "06:10:00"
            with pytest.raises(ToolError, match="Supported date range: 2026-06-01 to 2026-06-30"):
                await client.call_tool(
                    "get_departures", departure_arguments | {"date": "2026-07-01"}
                )
            routes = await client.call_tool("get_routes_for_stop", {"stop_id": "S2"})
            assert [r["route_id"] for r in routes.structured_content["result"]] == ["R1", "R2"]

    asyncio.run(check())


# --------------------------------------------------------------------------- #
# list_routes_and_stops resource
# --------------------------------------------------------------------------- #


def _ctx_with(schedule: ZTMStaticSchedule) -> SimpleNamespace:
    """Minimal stand-in for a fastmcp Context exposing the lifespan storage."""
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"ztm_static_storage": schedule})
    )


def test_list_routes_and_stops_empty_storage():
    out = list_routes_and_stops(_ctx_with(ZTMStaticSchedule.instance()))
    assert out == {"routes": [], "stops": []}


def test_list_routes_and_stops_reflects_storage(sample_gtfs):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert {r["route_id"] for r in out["routes"]} == {"R1", "R2"}
    assert {s["stop_id"] for s in out["stops"]} == {"S1", "S2", "S3"}


def test_list_routes_and_stops_returns_plain_json_types(sample_gtfs):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert type(out["routes"]) is list
    assert type(out["routes"][0]) is dict
    assert type(out["stops"]) is list
    assert type(out["stops"][0]) is dict


# --------------------------------------------------------------------------- #
# Integration: real service output flowing through storage into the resource
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_service_to_storage_to_resource_pipeline(real_gtfs):
    """Mirror the production path: ZTMService loads -> storage holds -> resource reads."""
    schedule = ZTMStaticSchedule.load(real_gtfs)

    out = list_routes_and_stops(_ctx_with(schedule))
    assert len(out["routes"]) == len(real_gtfs["routes"])
    assert len(out["stops"]) == len(real_gtfs["stops"])
    # Diacritics survive the full round trip.
    names = "".join(s.get("stop_name", "") for s in out["stops"])
    assert any(ch in names for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
